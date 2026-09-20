import time, json, sys
import locale
from enum import Enum
from multiprocessing import shared_memory, resource_tracker
import numpy as np
from typing import Union
import asyncio
import logging
import socket

import typer
from rich.console import Console
from rich.logging import RichHandler
from panoseti_grpc.daq_data.client import AioDaqDataClient
import signal

from panoseti_grpc.telemetry.logger import get_logger

SOCK_PATH = "/tmp/panoseti_meta.sock"


class Mode(str, Enum):
    mov8 = "mov8"
    mov16 = "mov16"
    ph256 = "ph256"
    ph1024 = "ph1024"

class DaqDataBackend(object):
    def __init__(self, host: str, port: int, mode: str) -> None:
        # create logger
        self.logger = get_logger('pseti_gui.grpc_process', log_dir='/var/log/panoseti')
        # This process's stdout is a pipe to the parent QProcess, not a real
        # terminal -- get_logger()'s RichHandler would otherwise auto-detect
        # that and print plain, uncolored text. mainwin.py's grpc_stdout()
        # just relays these bytes verbatim to its own (real) terminal, so
        # force color here to match mainwin.py's own log output.
        for handler in self.logger.handlers:
            if isinstance(handler, RichHandler):
                handler.console = Console(force_terminal=True)
        self.logger.info('********************************************')
        self.logger.info('PANOSETI gRPC process started.')
        self.logger.info('********************************************')
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(SOCK_PATH)
        # Single-target connection: the server may be an edge DAQ node (dev/
        # single-machine setups) or a headnode/gateway that fans in multiple
        # edge nodes server-side -- pseti-gui doesn't need to know which.
        self.host = host
        self.port = port
        self.logger.info(f"host: {self.host}")
        self.logger.info(f"port: {self.port}")
        # get bytes_per_pixel
        self.mode = mode
        if mode == 'mov8':
            self.dtype = np.uint8
            self.size = 32
            self.bytes_per_pixel = 1
        elif mode == 'mov16':
            self.dtype = np.uint16
            self.size = 32
            self.bytes_per_pixel = 2
        elif mode == 'ph256':
            self.dtype = np.int16
            self.size = 16
            self.bytes_per_pixel = 2
        elif mode == 'ph1024':
            self.dtype = np.int16
            self.size = 32
            self.bytes_per_pixel = 2
        else:
            self.logger.error(f"Mode ({mode}) is not supported.")
            raise ValueError(f"Mode ({mode}) is not supported.")
        self.logger.info(f"mode is {mode}.")
        self.logger.info(f"img size is {self.size} x {self.size}.")
        self.logger.info(f"{self.bytes_per_pixel} bytes per pixel.")
        # create a shared memory, the size is size * size
        self.shm = shared_memory.SharedMemory(
            create=True,
            size=self.size * self.size * self.bytes_per_pixel
            )
        resource_tracker.unregister(self.shm._name, 'shared_memory')
        self.img = np.ndarray((self.size, self.size), dtype=self.dtype, buffer=self.shm.buf)

    def send_metadata(self, metadata):
        self.sock.sendall(json.dumps(metadata, default=str).encode() + b'\n')

    def send_shm_info(self):
        self.logger.info("Sending the shm info...")
        self.send_metadata({
                "shm": self.shm.name,
                "shape": [self.size, self.size],
                "mode": self.mode
            })

    async def send_images(self, ph_data=True, mov_data=False):
        self.logger.info("Sending images...")
        # The data source (real Hashpipe UDS acquisition or a simulation) is
        # initialized elsewhere -- production edge servers auto-start via
        # init_from_default, and `pseti start` is what triggers acquisition
        # otherwise. This process only attaches to an already-running stream.
        async with AioDaqDataClient(self.host, self.port, log_level=logging.DEBUG) as addc:
            stream_images_responses = addc.stream_images(
                stream_movie_data=mov_data,
                stream_pulse_height_data=ph_data,
                update_interval_seconds=1.0,
                module_ids=(),
                parse_pano_images=True,
                wait_for_ready=True,
            )
            async for parsed_pano_image in stream_images_responses:
                self.img[:] = parsed_pano_image['image_array']
                metadata = {k: v for k, v in parsed_pano_image.items() if k != "image_array"}
                self.send_metadata(metadata)

    def close(self):
        self.logger.info("Deattach the shm.")
        self.shm.close()
        try:
            #self.shm.unlink()
            pass
        except:
            self.logger.error('grpc_process failed.')
        self.logger.info("Deattached the shm.")

async def run(host: str, port: int, mode: str):
    backend = DaqDataBackend(host, port, mode)
    backend.send_shm_info()
    try:
        movie_mode = mode in ('mov8', 'mov16')
        await backend.send_images(ph_data=not movie_mode, mov_data=movie_mode)
    finally:
        backend.close()

app = typer.Typer(add_completion=False)


@app.command()
def main(
    host: str = typer.Option("localhost", "-o", "--host", help="Host of the panoseti_grpc server (edge DAQ node or headnode/gateway) to connect to."),
    port: int = typer.Option(50051, "-p", "--port", help="Port of the panoseti_grpc server to connect to."),
    mode: Mode = typer.Option(Mode.ph1024, "-m", "--mode", help="Mode options."),
) -> None:
    """Attach to a running panoseti_grpc DaqData stream and forward frames to pseti-gui over shared memory."""
    # Rich's log timestamps use the locale-dependent "%x %X" strftime format.
    # mainwin.py's process picks up the environment's locale as a side effect
    # of PyQt6's QApplication init (which calls setlocale(LC_ALL, "")); this
    # plain subprocess never touches locale otherwise, so without this it
    # stays on the terse default "C" locale format instead of matching
    # mainwin.py's log timestamps.
    locale.setlocale(locale.LC_TIME, '')
    # deal with SIGINT
    def handler(signum, frame):
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    # start!
    asyncio.run(run(host, port, mode.value))

if __name__ == '__main__':
    app()
