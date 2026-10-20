"""Async, read-only client for the LuxPower WiFi/LAN dongle.

Only Modbus read functions are implemented: 0x03 (read holding) and 0x04 (read
input). There is deliberately no code path in this module that can build or emit a
write function (0x06 / 0x10) -- adding one should be a conscious, reviewed change.

Deliberately free of Home Assistant imports so it can be exercised standalone
against a real dongle (see `python3 -m protocol_selftest` style usage in README).

Two behaviours of the dongle drive the design here:

* It serves one TCP client well at a time, so a single long-lived connection is
  held open and shared, rather than connecting per poll.
* It sends 0xC1 heartbeat frames and drops the connection if they are not echoed
  back, so a background reader task echoes them even while no read is pending.
"""

from __future__ import annotations

import asyncio
import logging
import struct

_LOGGER = logging.getLogger(__name__)

HEADER_PREFIX = b"\xa1\x1a"
TCP_HEARTBEAT = 0xC1
TCP_TRANSLATED = 0xC2
FUNC_READ_HOLD = 0x03
FUNC_READ_INPUT = 0x04

DEFAULT_PROTOCOL = 2
DEFAULT_TIMEOUT = 10.0
MAX_REGISTERS_PER_READ = 40

#: Input-register blocks that cover every verified sensor (see registers.py).
INPUT_BLOCKS: tuple[tuple[int, int], ...] = ((0, 40), (40, 40))

#: Input registers holding the inverter serial as little-endian ASCII byte pairs.
SERIAL_REGISTERS: tuple[int, int] = (115, 5)


class LuxPowerError(Exception):
    """Base error for dongle communication."""


class LuxPowerConnectionError(LuxPowerError):
    """The dongle could not be reached, or dropped the connection."""


class LuxPowerTimeout(LuxPowerError):
    """The dongle did not answer a read in time."""


# ----------------------------------------------------------------- framing --


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus over the device-level data section."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_read(
    dongle: str,
    inverter: str,
    func: int,
    start: int,
    count: int,
    protocol: int = DEFAULT_PROTOCOL,
) -> bytes:
    """Build a 'translated data' read request.

    Raises ValueError for anything that is not a read function, so a write can
    never be constructed through this helper by accident.
    """
    if func not in (FUNC_READ_HOLD, FUNC_READ_INPUT):
        raise ValueError(
            f"refusing to build function {func:#04x}: this client is read-only"
        )

    data = bytes([0x00, func]) + inverter.encode().ljust(10, b"\x00")[:10]
    data += struct.pack("<HH", start, count)
    data += struct.pack("<H", crc16_modbus(data))

    body = bytes([0x01, TCP_TRANSLATED]) + dongle.encode().ljust(10, b"\x00")[:10]
    body += struct.pack("<H", len(data)) + data

    # The length field counts everything after the first 6 bytes.
    return HEADER_PREFIX + struct.pack("<HH", protocol, len(body)) + body


class FrameReader:
    """Splits the TCP byte stream into whole LuxPower frames."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add bytes, return whatever complete frames that produced."""
        self._buf += chunk
        frames: list[bytes] = []
        while True:
            index = self._buf.find(HEADER_PREFIX)
            if index < 0:
                # Keep a trailing byte: it may be the first half of a prefix.
                self._buf = self._buf[-1:]
                break
            self._buf = self._buf[index:]
            if len(self._buf) < 6:
                break
            frame_len = struct.unpack_from("<H", self._buf, 4)[0]
            total = frame_len + 6
            if len(self._buf) < total:
                break
            frames.append(self._buf[:total])
            self._buf = self._buf[total:]
        return frames


def parse_frame(frame: bytes) -> dict:
    """Decode one frame into its header fields and, for read replies, registers."""
    parsed: dict = {
        "protocol": struct.unpack_from("<H", frame, 2)[0],
        "tcp_func": frame[7] if len(frame) > 7 else None,
        "dongle": frame[8:18].decode(errors="replace").strip("\x00"),
    }
    if parsed["tcp_func"] != TCP_TRANSLATED or len(frame) < 20:
        return parsed

    data_len = struct.unpack_from("<H", frame, 18)[0]
    data = frame[20 : 20 + data_len]
    if len(data) < 15:
        return parsed

    parsed["dev_func"] = data[1]
    parsed["inverter"] = data[2:12].decode(errors="replace").strip("\x00")
    parsed["start"] = struct.unpack_from("<H", data, 12)[0]

    if data[1] in (FUNC_READ_HOLD, FUNC_READ_INPUT) and data[0] == 0x01:
        value_len = data[14]
        values = data[15 : 15 + value_len]
        crc_ok = None
        if len(data) >= 15 + value_len + 2:
            crc_rx = struct.unpack_from("<H", data, 15 + value_len)[0]
            crc_ok = crc_rx == crc16_modbus(data[: 15 + value_len])
        parsed["registers"] = {
            parsed["start"] + n: struct.unpack_from("<H", values, n * 2)[0]
            for n in range(len(values) // 2)
        }
        parsed["crc_ok"] = crc_ok
    return parsed


# ------------------------------------------------------------------ client --


class LuxPowerClient:
    """Holds one connection to the dongle and serialises reads over it."""

    def __init__(
        self,
        host: str,
        port: int,
        dongle_serial: str,
        inverter_serial: str,
        protocol: int = DEFAULT_PROTOCOL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._dongle = dongle_serial
        self._inverter = inverter_serial
        self._protocol = protocol
        self._timeout = timeout

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._frames = FrameReader()
        # Keyed by (function, start, register count) so an unsolicited push frame
        # that happens to start at the same register cannot satisfy our request.
        self._waiters: dict[tuple[int, int, int], asyncio.Future] = {}
        self._request_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open the connection and start the background reader."""
        async with self._connect_lock:
            if self.connected:
                return
            await self._close()
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), self._timeout
                )
            except (OSError, asyncio.TimeoutError) as err:
                raise LuxPowerConnectionError(
                    f"could not connect to {self._host}:{self._port}: {err}. "
                    "Check the IP and that no other client is holding the dongle."
                ) from err
            self._frames = FrameReader()
            self._reader_task = asyncio.create_task(self._read_loop())
            _LOGGER.debug("Connected to dongle at %s:%s", self._host, self._port)

    async def disconnect(self) -> None:
        async with self._connect_lock:
            await self._close()

    async def _close(self) -> None:
        """Tear down the reader task and socket. Caller holds the connect lock."""
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass

        self._fail_waiters(LuxPowerConnectionError("connection closed"))

    def _fail_waiters(self, err: Exception) -> None:
        for future in list(self._waiters.values()):
            if not future.done():
                future.set_exception(err)
        self._waiters.clear()

    async def _read_loop(self) -> None:
        """Consume frames, echo heartbeats, hand replies to whoever is waiting."""
        assert self._reader is not None
        try:
            while True:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise LuxPowerConnectionError("dongle closed the connection")
                for frame in self._frames.feed(chunk):
                    self._handle_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - surfaced via waiters / next poll
            _LOGGER.debug("Reader loop stopped: %s", err)
            self._fail_waiters(
                err
                if isinstance(err, LuxPowerError)
                else LuxPowerConnectionError(str(err))
            )

    def _handle_frame(self, frame: bytes) -> None:
        try:
            parsed = parse_frame(frame)
        except (struct.error, IndexError) as err:
            _LOGGER.debug("Ignoring malformed frame: %s", err)
            return

        if parsed.get("tcp_func") == TCP_HEARTBEAT:
            # The dongle drops us if its heartbeats are not echoed back.
            if self._writer is not None and not self._writer.is_closing():
                self._writer.write(frame)
            return

        registers = parsed.get("registers")
        if registers is None:
            return

        key = (parsed.get("dev_func"), parsed.get("start"), len(registers))
        future = self._waiters.pop(key, None)  # type: ignore[arg-type]
        if future is None:
            # Unsolicited push frame. The dongle pushes banks whose layout has not
            # been verified against the app, so they are logged and dropped rather
            # than merged into state. Polled reads are the verified path.
            _LOGGER.debug(
                "Ignoring unsolicited frame: func=%s start=%s count=%s",
                parsed.get("dev_func"),
                parsed.get("start"),
                len(registers),
            )
            return

        if future.done():
            return
        if parsed.get("crc_ok") is False:
            future.set_exception(
                LuxPowerError(f"CRC mismatch on block at register {parsed['start']}")
            )
        else:
            future.set_result(registers)

    async def read_input(self, start: int, count: int) -> dict[int, int]:
        """Read `count` input registers starting at `start`."""
        return await self._read(FUNC_READ_INPUT, start, count)

    async def read_holding(self, start: int, count: int) -> dict[int, int]:
        """Read `count` holding registers starting at `start`."""
        return await self._read(FUNC_READ_HOLD, start, count)

    async def _read(self, func: int, start: int, count: int) -> dict[int, int]:
        if not 0 < count <= MAX_REGISTERS_PER_READ:
            raise ValueError(
                f"count must be 1..{MAX_REGISTERS_PER_READ}, got {count}"
            )

        request = build_read(
            self._dongle, self._inverter, func, start, count, self._protocol
        )

        async with self._request_lock:
            if not self.connected:
                await self.connect()
            assert self._writer is not None

            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            key = (func, start, count)
            self._waiters[key] = future
            try:
                self._writer.write(request)
                await self._writer.drain()
                return await asyncio.wait_for(future, self._timeout)
            except asyncio.TimeoutError as err:
                raise LuxPowerTimeout(
                    f"no reply for function {func:#04x} registers "
                    f"{start}-{start + count - 1}"
                ) from err
            except (OSError, ConnectionResetError) as err:
                raise LuxPowerConnectionError(f"write failed: {err}") from err
            finally:
                self._waiters.pop(key, None)

    async def async_read_all(self) -> dict[int, int]:
        """Read every block needed for the verified sensor set."""
        registers: dict[int, int] = {}
        for start, count in INPUT_BLOCKS:
            registers.update(await self.read_input(start, count))
        return registers


# --------------------------------------------------------------- discovery --


def decode_serial(registers: dict[int, int]) -> str | None:
    """Pull the inverter serial out of a read of SERIAL_REGISTERS.

    Each register holds two ASCII characters, low byte first, so register order
    gives the serial directly: 0x3133 -> "31", 0x3339 -> "93", ...
    """
    start, count = SERIAL_REGISTERS
    wanted = [start + n for n in range(count)]
    if not all(reg in registers for reg in wanted):
        return None
    try:
        raw = b"".join(struct.pack("<H", registers[reg]) for reg in wanted)
        serial = raw.decode("ascii").strip("\x00").strip()
    except (UnicodeDecodeError, struct.error):
        return None
    # Guard against decoding noise as a serial.
    if len(serial) != count * 2 or not serial.isalnum():
        return None
    return serial


async def read_inverter_serial(
    host: str, port: int, dongle_serial: str, timeout: float = DEFAULT_TIMEOUT
) -> str | None:
    """Ask the inverter for its own serial. Reliable, unlike passive listening.

    Verified against the hardware: the dongle ignores the inverter serial in a
    request -- blank, zero-filled and plain wrong values all get answered -- while
    it does validate its own serial, so `dongle_serial` has to be correct. That
    makes the inverter serial readable out of registers 115-119 up front.

    Returns None if the dongle answers nothing usable; raises
    LuxPowerConnectionError if it cannot be reached at all.
    """
    client = LuxPowerClient(host, port, dongle_serial, "", timeout=timeout)
    try:
        registers = await client.read_input(*SERIAL_REGISTERS)
    except LuxPowerConnectionError:
        # Could not reach the dongle at all. That is a different problem from a
        # serial we could not read, and the caller reports it differently, so it
        # must not be flattened into None here. Listed before LuxPowerError
        # because it is a subclass of it.
        raise
    except LuxPowerTimeout:
        # Most likely a wrong dongle serial: that is the one field it checks.
        _LOGGER.debug("No reply reading the serial; is the dongle serial right?")
        return None
    except LuxPowerError as err:
        _LOGGER.debug("Could not read the inverter serial: %s", err)
        return None
    finally:
        await client.disconnect()

    return decode_serial(registers)


async def discover_serials(
    host: str, port: int, timeout: float = 30.0
) -> tuple[str | None, str | None]:
    """Listen to the dongle's own traffic to learn the two serials.

    Unreliable by nature, and only worth using for the dongle serial: the dongle
    volunteers heartbeats and push frames on its own schedule and may say nothing
    for a long time. Once the dongle serial is known, prefer
    `read_inverter_serial()` for the inverter one.
    """
    dongle: str | None = None
    inverter: str | None = None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), DEFAULT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as err:
        raise LuxPowerConnectionError(
            f"could not connect to {host}:{port}: {err}"
        ) from err

    frames = FrameReader()
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not (dongle and inverter):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(4096), remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            for frame in frames.feed(chunk):
                parsed = parse_frame(frame)
                if parsed.get("tcp_func") == TCP_HEARTBEAT:
                    writer.write(frame)
                if not dongle and parsed.get("dongle"):
                    dongle = parsed["dongle"]
                candidate = parsed.get("inverter")
                if not inverter and candidate and candidate.strip("0"):
                    inverter = candidate
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass

    return dongle, inverter
