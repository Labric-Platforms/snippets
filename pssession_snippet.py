"""
Minimal dependency-free example code to help read PalmSens .pssession files;
please note that this is not complete and ready-to-run!

You'll need to fill in the stubs at the bottom to handle the data according
to your specific needs (e.g. write to a database, extract figures of merit,
plot for presentation, etc.)
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# .NET ticks are 100-nanosecond intervals since 0001-01-01T00:00:00Z.
DOTNET_TICKS_AT_UNIX_EPOCH = 621_355_968_000_000_000
DOTNET_TICKS_PER_SECOND = 10_000_000

# Unit["S"] only contains a base symbol, Unit["Type"] names the prefix.
# example: A would be in Unit["S"] for milliamp and microamp measurements 
SI_PREFIX_SCALES = {
    "Femto": 1e-15, "Pico": 1e-12, "Nano": 1e-9, "Micro": 1e-6,
    "Milli": 1e-3, "Centi": 1e-2, "Kilo": 1e3, "Mega": 1e6, "Giga": 1e9,
}

# ArrayType codes
TIME, POTENTIAL, CURRENT, CHARGE = 0, 1, 2, 3
FREQUENCY, PHASE, Z_REAL, Z_IMAGINARY, Z_MODULUS = 5, 6, 7, 8, 10


@dataclass
class Series:
    """One column of one measurement."""

    label: str            # Description; repeats across CV scans
    array_type: int       # see above ArrayType codes
    quantity: str | None  # e.g. "Potential", "Frequency", "-Z''"
    symbol: str           # base unit symbol, e.g. "V", "A", "Hz"
    unit_class: str       # e.g. "PalmSens.Units.MicroAmpere"
    scale_to_si: float    # multiply values by this to reach base units
    hidden: bool
    values: list[float] = field(repr=False, default_factory=list)  # as stored, in unit_class
    point_flags: list[dict] = field(repr=False, default_factory=list)

    @property
    def values_si(self) -> list[float]:
        """Values converted to SI base units (A, V, s, Hz, ohm)."""
        return [value * self.scale_to_si for value in self.values]


def load_session(path: str) -> dict:
    """
    Load a .pssession file as JSON.

    This probably isn't the approach you should use for
    very large files.
    """
    text = open(path, "rb").read().decode("utf-16")
    return json.loads(text.strip("\ufeff"))


def dotnet_ticks_to_datetime(ticks: int) -> datetime:
    seconds = (ticks - DOTNET_TICKS_AT_UNIX_EPOCH) / DOTNET_TICKS_PER_SECOND
    return datetime.fromtimestamp(seconds, timezone.utc)


def parse_method(method_text: str) -> tuple[dict[str, str], list[str]]:
    """
    Split the method block into KEY=VALUE settings and any "#" header lines.
    """
    settings, header = {}, []
    for line in method_text.split("\r\n"):
        line = line.strip()
        if line.startswith("#"):
            header.append(line[1:])
        elif "=" in line:
            key, value = line.split("=", 1)
            settings[key] = value
    return settings, header


def scale_to_si(unit_class: str) -> float:
    match = re.search(r"PalmSens\.Units\.(%s)" % "|".join(SI_PREFIX_SCALES), unit_class)
    return SI_PREFIX_SCALES[match.group(1)] if match else 1.0


def read_series(
    measurement: dict, include_hidden: bool = False, keep_point_flags: bool = False
) -> list[Series]:
    """
    Read the raw data arrays, which are the authoritative copy.

    Note that Curves and EISDataList restate the same points for plotting
    and don't need to both be read as a result
    """
    series = []
    for array in measurement["DataSet"]["Values"]:
        if array.get("Hidden") and not include_hidden:
            continue
        unit = array["Unit"]
        series.append(
            Series(
                label=array["Description"],
                array_type=array["ArrayType"],
                quantity=unit["Q"],
                symbol=unit["S"],
                unit_class=unit["Type"],
                scale_to_si=scale_to_si(unit["Type"]),
                hidden=bool(array.get("Hidden")),
                values=[point["V"] for point in array["DataValues"]],
                point_flags=(
                    [
                        {k: v for k, v in point.items() if k != "V"}
                        for point in array["DataValues"]
                    ]
                    if keep_point_flags
                    else []
                ),
            )
        )
    return series


def select(series: list[Series], array_type: int, label: str | None = None) -> Series | None:
    """
    Find one series by its ArrayType code.
    """
    for candidate in series:
        if candidate.array_type == array_type and label in (None, candidate.label):
            return candidate
    return None


def to_rows(series: list[Series]) -> list[dict]:
    """
    Zip aligned series into per-point records.
    """
    if not series:
        return []
    names, seen = [], {}
    for candidate in series:
        name = f"{candidate.label}__{candidate.quantity or candidate.symbol}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}__{seen[name]}")
    return [
        {name: candidate.values[i] for name, candidate in zip(names, series)}
        for i in range(len(series[0].values))
    ]


def group_cyclic_voltammetry_scans(series: list[Series]) -> list[list[Series]]:
    """
    Split a voltammogram's series into one block per scan.
    """
    scanned = [s for s in series if s.array_type != TIME]
    scan_count = len({s.label for s in scanned if s.label.startswith("scan")})
    if not scan_count:
        return [scanned]
    block_size = len(scanned) // scan_count
    return [scanned[i:i + block_size] for i in range(0, len(scanned), block_size)]


def read_measurement(measurement: dict, keep_point_flags: bool = False) -> dict:
    settings, header = parse_method(measurement["Method"])
    return {
        "title": measurement["Title"],
        "measured_at": dotnet_ticks_to_datetime(measurement["UTCTimeStamp"]),
        "instrument_serial": measurement["DeviceSerial"],
        "firmware": measurement["DeviceFW"],
        "method_id": settings.get("METHOD_ID"),
        "settings": settings,
        "method_header": header,
        "series": read_series(measurement, keep_point_flags=keep_point_flags),
    }


# --- Your handlers should go below ------------------------------------------------------
# (Each handler takes the dict from read_measurement)

def handle_cyclic_voltammetry(measurement: dict) -> None:
    elapsed = select(measurement["series"], TIME)
    for scan_number, scan in enumerate(group_cyclic_voltammetry_scans(measurement["series"]), 1):
        potential = select(scan, POTENTIAL)
        current = select(scan, CURRENT)
        ...  # TODO: use one scan


def handle_impedance_spectroscopy(measurement: dict) -> None:
    frequency = select(measurement["series"], FREQUENCY)
    z_real = select(measurement["series"], Z_REAL)
    z_imaginary = select(measurement["series"], Z_IMAGINARY)
    ...  # TODO: use the spectrum


def handle_time_series(measurement: dict) -> None:
    elapsed = select(measurement["series"], TIME)
    potential = select(measurement["series"], POTENTIAL)
    current = select(measurement["series"], CURRENT)
    ...  # TODO: use the trace


HANDLERS = {
    "cv": handle_cyclic_voltammetry,
    "eis": handle_impedance_spectroscopy,
    "ad": handle_time_series,
    "ocp": handle_time_series,
}


def read_pssession(path: str) -> None:
    for raw_measurement in load_session(path)["Measurements"]:
        measurement = read_measurement(raw_measurement)
        handler = HANDLERS.get(measurement["method_id"])
        if handler is None:
            raise ValueError(f"Unsupported technique: {measurement['method_id']}")
        handler(measurement)
