#!/usr/bin/env python3
"""Таймер интервью AI System Design: окно без рамки поверх всех окон.

    python3 tools/timer.py start [--speed 60] [--at 33]  новый отсчёт (ускорение, стартовая минута)
    python3 tools/timer.py open                           открыть окно; время не сбрасывается
    python3 tools/timer.py stop                           остановить отсчёт и закрыть окно
    python3 tools/timer.py status                         этап и время одной строкой
    python3 tools/timer.py active                         код 0, если интервью идёт (< 3 ч)

Окно: тащить мышью, двойной клик — компактный режим, правая кнопка — меню.
Только стандартная библиотека; tkinter берётся из системного Python (/usr/bin/python3).
"""

import argparse
import fcntl
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
STATE = LOCAL / "timer.json"
LOCK = LOCAL / "timer.lock"
LOG = LOCAL / "timer.log"
TOTAL = 120 * 60


class Step(NamedTuple):
    start: int  # минута от начала интервью
    end: int
    name: str
    hint: str


class Stage(NamedTuple):
    start: int
    end: int
    name: str
    hint: str
    color: str
    steps: tuple[Step, ...]


STAGES = (
    Stage(0, 35, "Дизайн системы", "Ревью RFC джуна и архитектура (AI — по желанию)", "#4c8bf5", (
        Step(0, 7, "Уточнение ФТ/НФТ", "Сверить скоуп, ФТ и НФТ из RFC джуна; задать вопросы"),
        Step(7, 12, "Расчёты", "RPS/TPS чтения и записи, пик, storage, трафик"),
        Step(12, 17, "API", "2–4 ключевые ручки: метод, путь, тело, коды ответов"),
        Step(17, 21, "Данные", "Таблицы, ключи, индексы → можно отдать агенту шаг 1"),
        Step(21, 30, "Схема и масштабирование",
             "Кэш, очередь, S3, реплики, шардирование, корнер-кейсы"),
        Step(30, 33, "Фиксация дизайна", "Что меняем в RFC джуна; на 33' дизайн фиксируется"),
        Step(33, 35, "План MVP", "plan.md: prod → MVP, шаги для агента, первый промпт"),
    )),
    Stage(35, 80, "Реализация с AI", "Ключевой компонент: happy path + критичные интеграции",
          "#34a853", (
        Step(35, 40, "Каркас", "Модели + make migration; агент работает — вы комментируете"),
        Step(40, 55, "Ключевые ручки", "Ручки happy path; curl после каждого шага"),
        Step(55, 65, "Интеграции", "Кэш / очередь jobs / S3 — только критичные"),
        Step(65, 73, "Демо-сценарий", "demo.sh: curl по happy path; тест — если просят"),
        Step(73, 78, "Стабилизация", "Починить; упрощения prod → MVP записать в plan.md"),
        Step(78, 80, "Показ MVP", "78': фиксируем реализацию, make demo"),
    )),
    Stage(80, 100, "Фрагмент руками", "Код без AI: структура данных или алгоритм", "#f2a900", (
        Step(80, 83, "Разбор задачи", "Вход/выход, крайние случаи, сложность — вслух"),
        Step(83, 95, "Пишем код", "Без AI; агент может доделывать MVP в фоне"),
        Step(95, 100, "Проверка", "Прогнать примеры и крайние случаи, назвать O(·)"),
    )),
    Stage(100, 110, "Финальные вопросы", "2–3 вопроса от интервьюера", "#a142f4", (
        Step(100, 110, "Вопросы интервьюера", "Коротко: trade-offs, масштабирование, отказы"),
    )),
    Stage(110, 120, "Финализация артефактов", "Код, тесты, ai-logs, spec.md, plan.md", "#ea4335", (
        Step(110, 115, "Артефакты", "make finish: commit + push (код, spec, plan, ai-logs)"),
        Step(115, 120, "Вопросы кандидата", "Свои вопросы интервьюеру"),
    )),
)
CHECKPOINTS = ((33, "фиксируем дизайн"), (78, "фиксируем реализацию"),
               (100, "переходим к вопросам"), (120, "время вышло"))  # fmt: skip


# ---------- состояние ----------


def new_state(speed: float = 1, at_min: float = 0) -> dict:
    return {"start": time.time(), "speed": speed, "offset": at_min * 60, "paused_at": None}


def fresh(speed: float = 1, at_min: float = 0) -> dict:
    """Новый отсчёт с сохранением позиции окна и режима."""
    old = load_raw()
    return {**new_state(speed, at_min), "pos": old.get("pos"), "compact": old.get("compact")}


def load_raw() -> dict:
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return s if isinstance(s, dict) else {}


def load() -> dict | None:
    """Текущее состояние; None — таймер не запущен или остановлен."""
    s = load_raw()
    return s if "start" in s and not s.get("stopped") else None


def save(state: dict) -> None:
    LOCAL.mkdir(exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE)


def elapsed(state: dict, now: float | None = None) -> float:
    """Секунды интервью с учётом ускорения, сдвига и паузы."""
    end = state.get("paused_at") or (time.time() if now is None else now)
    return max(0.0, (end - state["start"]) * state.get("speed", 1) + state.get("offset", 0))


def where(t: float) -> tuple[Stage, Step]:
    for stage in STAGES:
        for step in stage.steps:
            if t < step.end * 60:
                return stage, step
    return STAGES[-1], STAGES[-1].steps[-1]


def fmt(seconds: float) -> str:
    s = int(abs(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def status_line(state: dict | None) -> str:
    if state is None:
        return "Таймер не запущен"
    t = elapsed(state)
    stage, step = where(t)
    return f"{stage.name} › {step.name} · T+{fmt(t)}"


# ---------- окно ----------


def _dim(color: str, k: float = 0.35) -> str:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{int(c * k + 0x1E * (1 - k)):02x}" for c in (r, g, b))


def primary_monitor() -> tuple[int, int, int, int] | None:
    """(x, y, w, h) основного монитора по xrandr; None — не удалось узнать."""
    try:
        cmd = ["xrandr", "--query"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if " connected primary " in line:
            try:  # у выключенного монитора геометрии нет
                geom = line.split(" connected primary ")[1].split()[0]  # 4384x2466+4384+0
                size, x, y = geom.split("+")
                w, h = size.split("x")
                return int(x), int(y), int(w), int(h)
            except (IndexError, ValueError):
                return None
    return None


class Window:
    BG, FG, DIM, AMBER = "#1e2430", "#f2f4f8", "#9aa4b2", "#ffb020"
    WARN_BG, ALERT_BG, PAUSE_BG = "#5c4600", "#8b1e24", "#3b4049"
    FONT = "DejaVu Sans"

    def __init__(self, root) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = root
        self.k = max(1.0, root.winfo_fpixels("1i") / 96)  # масштаб под HiDPI
        self.W = self.px(500)
        self.state = load() or new_state()
        self.compact = bool(self.state.get("compact"))
        self.menu_open_until = 0.0
        self.drag_from = (0, 0)
        root.overrideredirect(True)  # без рамки: WM не свернёт и не спрячет окно
        root.attributes("-topmost", True)
        self.canvas = tk.Canvas(root, width=self.W, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.menu = tk.Menu(root, tearoff=False, font=(self.FONT, -self.px(14)))
        # Только на canvas: bind_all ловил бы и клики внутри меню.
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", lambda e: self.set_compact(not self.compact))
        self.canvas.bind("<Button-3>", self.on_menu)
        pos = self.state.get("pos")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        if pos and not (0 <= pos[0] < sw - self.px(50) and 0 <= pos[1] < sh - self.px(30)):
            pos = None  # раскладка мониторов изменилась — окно оказалось бы за экраном
        if not pos:
            mon = primary_monitor() or (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
            pos = (mon[0] + mon[2] - self.W - self.px(30), mon[1] + self.px(60))
        root.geometry(f"+{pos[0]}+{pos[1]}")
        self.tick()

    def px(self, v: float) -> int:
        return round(v * self.k)

    # --- действия ---

    def update_state(self, **changes) -> None:
        self.state = {**(load() or self.state), **changes}
        save(self.state)

    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        self.update_state(compact=compact)

    def toggle_pause(self) -> None:
        s = load() or self.state
        now = time.time()
        if s.get("paused_at"):
            self.update_state(start=s["start"] + now - s["paused_at"], paused_at=None)
        else:
            self.update_state(paused_at=now)

    def shift(self, minutes: int) -> None:
        s = load() or self.state
        self.update_state(offset=s.get("offset", 0) + minutes * 60)

    def restart(self) -> None:
        save(fresh((load() or self.state).get("speed", 1)))

    # --- мышь ---

    def on_press(self, e) -> None:
        self.drag_from = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def on_drag(self, e) -> None:
        self.root.geometry(f"+{e.x_root - self.drag_from[0]}+{e.y_root - self.drag_from[1]}")

    def on_release(self, _e) -> None:
        pos = [self.root.winfo_x(), self.root.winfo_y()]
        if pos != self.state.get("pos"):
            self.update_state(pos=pos)

    def on_menu(self, e) -> None:
        m, paused = self.menu, bool((load() or self.state).get("paused_at"))
        m.delete(0, "end")
        m.add_command(label="Продолжить" if paused else "Пауза", command=self.toggle_pause)
        m.add_command(label="+1 минута", command=lambda: self.shift(1))
        m.add_command(label="−1 минута", command=lambda: self.shift(-1))
        m.add_command(label="Полный режим" if self.compact else "Компактный режим",
                      command=lambda: self.set_compact(not self.compact))  # fmt: skip
        m.add_separator()
        reset = self.tk.Menu(m, tearoff=False, font=(self.FONT, -self.px(14)))
        reset.add_command(label="Да, начать с 00:00", command=self.restart)
        m.add_cascade(label="Сбросить…", menu=reset)
        m.add_command(label="Закрыть окно (время идёт)", command=self.root.destroy)
        self.menu_open_until = time.time() + 15  # не поднимать окно поверх открытого меню
        m.bind("<Unmap>", lambda _e: setattr(self, "menu_open_until", 0.0))
        m.tk_popup(e.x_root, e.y_root)

    # --- отрисовка ---

    def tick(self) -> None:
        try:
            self.render()
        except Exception:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        try:
            self.root.after(250, self.tick)
        except self.tk.TclError:
            pass  # окно уже закрыто

    def text(self, x: float, y: float, s: str, size: int, *, bold=False, fg=None, right=False,
             wrap: float = 0) -> int:  # fmt: skip
        return self.canvas.create_text(
            self.px(x), self.px(y), text=s, anchor="ne" if right else "nw", fill=fg or self.FG,
            font=(self.FONT, -self.px(size), "bold" if bold else "normal"), width=self.px(wrap),
        )  # fmt: skip

    def fit(self, item: int, size: int, max_right: int) -> None:
        """Уменьшать шрифт, пока текст не влезет левее max_right."""
        while self.canvas.bbox(item)[2] > max_right and size > 12:
            size -= 1
            self.canvas.itemconfigure(item, font=(self.FONT, -self.px(size), "bold"))

    def render(self) -> None:
        raw = load_raw()  # {} если файл на миг пропал — рисуем по последнему состоянию
        if raw.get("stopped"):
            self.root.destroy()
            return
        if "start" in raw:
            self.state = raw
        self.compact = bool(self.state.get("compact"))

        t = elapsed(self.state)
        stage, step = where(t)
        over = t >= TOTAL
        paused = bool(self.state.get("paused_at"))
        speed = self.state.get("speed", 1)
        cp = next(((m, txt) for m, txt in CHECKPOINTS if t < m * 60), None)
        hit = next(((m, txt) for m, txt in CHECKPOINTS if 0 <= t - m * 60 < 60), None)
        warn = cp is not None and cp[0] * 60 - t <= 120
        bg = self.PAUSE_BG if paused else self.ALERT_BG if over or hit else (
            self.WARN_BG if warn else self.BG)  # fmt: skip

        steps = [s for st in STAGES for s in st.steps]
        nxt = steps[steps.index(step) + 1] if step is not steps[-1] else None
        left = step.end * 60 - t
        clock = "+" + fmt(t - TOTAL) if over else fmt(left)
        clock_fg = self.AMBER if 0 < left <= 60 and not paused else self.FG
        tags = " · ".join(filter(None, ["ПАУЗА" if paused else "",
                                        f"×{speed:g} тест" if speed != 1 else ""]))  # fmt: skip

        c, R = self.canvas, 500 - 14
        c.delete("all")
        if self.compact:
            h = 62
            clock_id = self.text(R, 4, clock, 24, bold=True, fg=clock_fg, right=True)
            name = self.text(14, 8, "ВРЕМЯ ВЫШЛО" if over else step.name, 19, bold=True)
            self.fit(name, 19, c.bbox(clock_id)[0] - self.px(12))
            self.draw_bar(t, 42)
            if tags:
                self.text(R, 46, tags, 9, bold=True, right=True)
        else:
            h = 236
            n = STAGES.index(stage) + 1
            if over:
                self.text(14, 10, "ВРЕМЯ ВЫШЛО", 15, bold=True)
            else:
                self.text(14, 10, f"{stage.name.upper()} · {n}/{len(STAGES)}", 15, bold=True)
                self.text(R, 10, f"этап {fmt(stage.end * 60 - t)}", 15, right=True)
            self.text(14, 33, stage.hint, 12, fg=self.DIM)
            clock_id = self.text(R, 54, clock, 36, bold=True, fg=clock_fg, right=True)
            name = self.text(14, 60, "Сверх времени" if over else step.name, 24, bold=True)
            self.fit(name, 24, c.bbox(clock_id)[0] - self.px(12))
            hint = f"■ {hit[0]}' — {hit[1].upper()}" if hit else step.hint
            hint = "Заканчиваем: make finish" if over and not hit else hint
            self.text(14, 100, hint, 15, bold=bool(hit), wrap=472)
            if nxt and not over:
                self.text(14, 146, f"Далее: {nxt.name} · {nxt.end - nxt.start} мин", 13,
                          fg=self.DIM)  # fmt: skip
            if cp and not over:
                self.text(14, 168, f"◆ {cp[0]}' {cp[1]} — через {fmt(cp[0] * 60 - t)}", 13,
                          bold=True)  # fmt: skip
            self.text(R, 168, f"T+{fmt(t)} / {fmt(TOTAL)}", 13, fg=self.DIM, right=True)
            self.draw_bar(t, 194)
            self.text(14, 212, "ПКМ — меню · двойной клик — компактно", 10, fg=self.DIM)
            if tags:
                self.text(R, 212, tags, 11, bold=True, fg=self.AMBER, right=True)
        c.configure(bg=bg, height=self.px(h))
        self.root.configure(bg=bg)

        if time.time() > self.menu_open_until:
            self.root.attributes("-topmost", True)
            self.root.lift()  # без focus_force: клавиатура остаётся в редакторе

    def draw_bar(self, t: float, y: float) -> None:
        c, x0, w, h = self.canvas, self.px(14), self.px(472), self.px(12)
        y = self.px(y)
        x = lambda sec: x0 + w * min(sec, TOTAL) / TOTAL  # noqa: E731
        for stage in STAGES:
            a, b = x(stage.start * 60), x(stage.end * 60)
            c.create_rectangle(a, y, b, y + h, fill=_dim(stage.color), width=0)
            if t > stage.start * 60:
                c.create_rectangle(a, y, min(b, x(t)), y + h, fill=stage.color, width=0)
            for step in stage.steps[1:]:
                c.create_line(x(step.start * 60), y, x(step.start * 60), y + h, fill="#10131a")
        for m, _ in CHECKPOINTS[:-1]:
            c.create_line(x(m * 60), y - self.px(3), x(m * 60), y + h + self.px(3), fill="#ffffff")
        c.create_rectangle(x(t) - self.px(1.5), y - self.px(4), x(t) + self.px(1.5),
                           y + h + self.px(4), fill="#ffffff", width=0)  # fmt: skip


def run_gui() -> int:
    import tkinter as tk

    LOCAL.mkdir(exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # уже открыт
        if load() is None:
            save(fresh())
        root = tk.Tk()
        root.title("Таймер интервью")
        Window(root)
        root.mainloop()
    return 0


def is_open() -> bool:
    LOCAL.mkdir(exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def spawn() -> int:
    """Открыть окно отдельным процессом: закрытие терминала его не убьёт."""
    if is_open():
        print(f"Таймер уже открыт · {status_line(load())}")
        return 0
    with LOG.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "gui"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )  # fmt: skip
    time.sleep(0.8)
    if proc.poll() is not None:
        print(f"Не удалось открыть таймер (нужны DISPLAY и tkinter). Лог: {LOG}", file=sys.stderr)
        return 1
    print(f"Таймер открыт · {status_line(load())}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start", help="новый отсчёт")
    start.add_argument("--speed", type=float, default=1, help="ускорение, например 60")
    start.add_argument("--at", type=float, default=0, help="начать с этой минуты")
    for name in ("open", "stop", "status", "active", "gui"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    if args.cmd == "start":
        save(fresh(args.speed, args.at))
        return spawn()
    if args.cmd == "open":
        if load() is None:
            save(fresh())
        return spawn()
    if args.cmd == "stop":
        save({**load_raw(), "stopped": True})
        print("Таймер остановлен")
        return 0
    if args.cmd == "status":
        print(status_line(load()))
        return 0
    if args.cmd == "active":
        state = load()
        return 0 if state is not None and elapsed(state) < 3 * 3600 else 1
    return run_gui()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
