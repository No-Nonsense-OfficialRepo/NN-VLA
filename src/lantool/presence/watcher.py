"""F5: 'playing' is detected from the game's exe process, not the launcher."""
from __future__ import annotations
import asyncio, csv, io, subprocess, sys

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def list_processes() -> set[str]:
    """Lower-cased running exe names. psutil if installed, else `tasklist` on Windows."""
    try:
        import psutil
        return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
    except ImportError:
        pass
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                             text=True, creationflags=_NO_WINDOW).stdout
        return {row[0].lower() for row in csv.reader(io.StringIO(out)) if row}
    return set()


class ProcessWatcher:
    """watch: {game_id: exe_name}. Calls on_launch(game)/on_exit(game) (sync or async).
    A game counts as exited only after `miss_limit` consecutive polls without its process (launchers re-spawn)."""
    def __init__(self, watch, on_launch, on_exit, list_fn=list_processes, interval=2.0, miss_limit=2):
        self.watch = {g: e.lower() for g, e in watch.items()}
        self.on_launch, self.on_exit, self.list_fn = on_launch, on_exit, list_fn
        self.interval, self.miss_limit = interval, miss_limit
        self.running: set[str] = set()
        self._misses: dict[str, int] = {}
        self._task = None

    async def _fire(self, cb, game):
        r = cb(game)
        if asyncio.iscoroutine(r):
            await r

    async def poll_once(self):
        procs = await asyncio.get_running_loop().run_in_executor(None, self.list_fn)
        for game, exe in self.watch.items():
            if exe in procs:
                self._misses[game] = 0
                if game not in self.running:
                    self.running.add(game)
                    await self._fire(self.on_launch, game)
            elif game in self.running:
                self._misses[game] = self._misses.get(game, 0) + 1
                if self._misses[game] >= self.miss_limit:
                    self.running.discard(game)
                    await self._fire(self.on_exit, game)

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            await self.poll_once()
            await asyncio.sleep(self.interval)

    def stop(self):
        if self._task:
            self._task.cancel()
