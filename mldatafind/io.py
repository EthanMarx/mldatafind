import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

import numpy as np
from gwpy.timeseries import TimeSeries, TimeSeriesDict

PATH_LIKE = Union[str, Path]
MAYBE_PATHS = Union[PATH_LIKE, Iterable[PATH_LIKE]]

prefix_re = "[a-zA-Z0-9_:-]+"
t0_re = "[0-9]{10}"
length_re = "[1-9][0-9]{0,3}"
fname_re = re.compile(
    f"(?P<prefix>{prefix_re})-"
    f"(?P<t0>{t0_re})-"
    f"(?P<length>{length_re})"
    ".(?P<suffix>gwf|hdf5|h5)$"
)


def filter_and_sort_files(
    fnames: MAYBE_PATHS,
    t0: Optional[float] = None,
    tf: Optional[float] = None,
    return_matches: bool = False,
) -> List[PATH_LIKE]:
    """Sort data files by their timestamps

    Given a list of filenames or a directory name containing
    data files, sort the files by their timestamps, assuming
    they follow the convention <prefix>-<timestamp>-<length>.hdf5

    If `t0` is specified, only return files that contain data
    with timestamps greater than `t0`. Additionally, if `tf` is specified
    only return matches with timestamps less than `tf`. If both `t0` and `tf`
    are specified, matches containing any data in the range `t0` to `tf` will
    be returned.

    Args:
        fnames:
            Path to directory containing files,
            or iterable of paths to sort
        t0:
            return files that contain data greater than this gpstime
        tf:
            return files that contain data less than this gpstime
        return_matches:
            If true return the match objects, otherwise return file names

    returns paths or match objects of sorted files
    """

    if isinstance(fnames, (Path, str)):
        fname_path = Path(fnames)
        if not fname_path.is_dir():
            # if this is not a directory
            # this is a single path to a file;
            # add it to a list and move on
            fnames = [fname_path]
            fname_it = [fname_path.name]
        else:
            # if we passed a single string or path,
            # that is a directory, asume this refers
            # to directory containing files we're meant
            # to sort
            fnames = list(fname_path.iterdir())
            fname_it = [f.name for f in fnames]
    else:
        # otherwise make sure the iterable contains either
        # _all_ Paths or _all_ strings. If all paths, normalize
        # them to just include the terminal filename
        if all([isinstance(i, Path) for i in fnames]):
            fname_it = [f.name for f in fnames]
        elif not all([isinstance(i, str) for i in fnames]):
            raise ValueError(
                "'fnames' must either be a path to a directory "
                "or an iterable containing either all strings "
                "or all 'pathlib.Path' objects, instead found "
                + ", ".join([type(i) for i in fnames])
            )
        else:
            fname_it = [Path(f).name for f in fnames]

    fnames = np.array(fnames)
    matches = np.array(list(map(fname_re.search, fname_it)))

    # downselect to paths that contain requested data
    mask = np.ones(len(matches), dtype=bool)

    if tf is not None:
        mask &= np.array([float(match.group("t0")) < tf for match in matches])

    if t0 is not None:
        stops = np.array(
            [
                float(match.group("length")) + float(match.group("t0"))
                for match in matches
            ]
        )
        mask &= stops > t0

    matches = matches[mask]
    fnames = fnames[mask]

    # use the timestamps from all valid timestamped filenames
    # to sort the files as the first index in a tuple
    tups = [
        (m.group("t0"), f, m) for m, f in zip(matches, fnames) if m is not None
    ]

    # if return_matches is True, return the match object,
    # otherwise just return the raw filename
    return_idx = 2 if return_matches else 1
    return [t[return_idx] for t in sorted(tups)]


def _validate_ts_dict(ts_dict: TimeSeriesDict):
    """Ensures all channels in TimeSeriesDict
    have the same t0, sample_rate, and length
    """
    timeseries_params = [
        (ts.t0.value, ts.dt, len(ts)) for ts in ts_dict.values()
    ]
    unique_ts_params = set(timeseries_params)
    if len(unique_ts_params) != 1:
        raise ValueError(
            "Channels in TimeSeriesDict must have the same t0, sample rate,"
            f"and length. Found {len(unique_ts_params)} "
            f"different combinations: {unique_ts_params}"
        )


def ts_dict_to_array(ts_dict: TimeSeriesDict):
    """Convert a TimeSeriesDict to an array.
    All channels in TimeSeriesDict are expected
    to have the same sample rate, t0, and length

    Args:
        ts_dict: TimeSeriesDict

    Returns array of channels, array of times
    """

    _validate_ts_dict(ts_dict)

    # get one ts so we can extract the times
    ts = ts_dict[list(ts_dict.keys())[0]]
    times = ts.times.value

    data = np.stack([ts.value for ts in ts_dict.values()])

    return data, times


def read_timeseries(
    path: MAYBE_PATHS,
    channels: List[str],
    t0: Optional[float] = None,
    tf: Optional[float] = None,
    array_like: bool = False,
) -> Union[TimeSeriesDict, Tuple[np.ndarray, np.ndarray]]:
    """
    Read multiple channel timeseries from hdf5 or gwf
    files into a TimeSeriesDict, or, if `array_like` is True,
    a tuple of numpy arrays where the first element is an array
    of the channels, and the second element is an array of corresponding times.
    Thin wrapper around TimeSeriesDict.read

    Args:
        path:
            File path, Iterable of file paths,
            or directory containing file paths to read
        channels:
            Channel names to read
        t0:
            Start gpstime to read.
            If not passed will begin reading from earliest found time
        tf:
            Stop gpstime to read.
            If not passed will read until latest found time
        array_like:
            Return in array like format.
            Otherwise, return gwpy.TimeSeriesDict

    Returns gwpy.TimeSeriesDict or Tuple of np.ndarrays
    """

    # downselect to files containing requested range
    paths = filter_and_sort_files(path, t0, tf)

    # this call will raise error if
    # channel doesn't exist,
    # if any channel doesnt contain
    # data from t0 to tf, or if gaps exist
    ts_dict = TimeSeriesDict.read(paths, channels, start=t0, end=tf)

    if not array_like:
        _validate_ts_dict(ts_dict)
        return ts_dict

    data, times = ts_dict_to_array(ts_dict)
    return data, times


def fetch_timeseries(
    channels: List[str],
    t0: float,
    tf: float,
    nproc: int = 1,
    array_like: bool = True,
) -> Union[TimeSeriesDict, Tuple[np.ndarray, np.ndarray]]:
    """
    Fetch multiple channel timeseries from nds2 and store TimeSeriesDict,
    or, if `array_lke` is True, a tuple of numpy arrays
    where the first element is an array of the channel data,
    and the second element an array of corresponding times.
    Thin wrapper around TimeSeriesDict.get

    Args:
        channels:
            Channel names to fetch
        t0:
            Start gpstime to read.
            If not passed will begin reading from earliest found time
        tf:
            Stop gpstime to read.
            If not passed will read until latest found time
        nproc:
            Number of concurrent processes to use with TimeSeriesDict.get
        array_like:
            Return in array like format. Otherwise, return gwpy.TimeSeriesDict

    Returns gwpy.TimeSeriesDict or Tuple of np.ndarrays
    """
    ts_dict = TimeSeriesDict.get(
        channels, start=t0, end=tf, nproc=nproc, verbose=True
    )
    if not array_like:
        _validate_ts_dict(ts_dict)
        return ts_dict

    data, times = ts_dict_to_array(ts_dict)
    return data, times


def _intify(x: float):
    return int(x) if int(x) == x else x


def write_timeseries(
    write_dir: Path,
    times: np.ndarray,
    prefix: str,
    file_format: str = "hdf5",
    **datasets,
) -> Path:
    """
    Write multi-channel timeseries to specified format (either gwf or h5).
    Thin wrapper around gwpy.TimeSeriesDict.write

    Args:
        write_dir:
            Path to directory to write files
        times:
            gpstimes corresponding to datasets
        prefix:
            Prefix used for file name

    Returns path to output file
    """

    if file_format not in ["hdf5", "gwf"]:
        raise ValueError(f"Writing to {format} format is not supported")

    # ensure all channels have same length
    n_samples = [len(dataset) for dataset in datasets.values()]

    if len(set(n_samples)) != 1:
        raise ValueError("Channels must all be of the same length")

    length = times[-1] - times[0] + times[1] - times[0]
    t0 = times[0]

    t0 = _intify(t0)
    length = _intify(length)

    # package data into TimeSeriesDict
    ts_dict = TimeSeriesDict()
    sample_rate = 1 / (times[1] - times[0])
    for channel, dataset in datasets.items():
        ts_dict[channel] = TimeSeries(dataset, dt=1 / sample_rate, t0=t0)

    # format the filename and write the data to an archive
    fname = write_dir / f"{prefix}-{t0}-{length}.hdf5"

    ts_dict.write(fname)

    return fname
