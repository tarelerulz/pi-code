"""Context budget: keep the chat history small enough for the model."""
from .results import save_result
from .ui import dim


def msg_chars(m):
    """Rough size of one message; attached images/audio count as 1000."""
    c = m.get("content")
    if isinstance(c, list):
        n = sum(len(p.get("text", "")) if p.get("type") == "text" else 1000 for p in c)
    else:
        n = len(c or "")
    for tc in m.get("tool_calls") or []:
        n += len(tc["function"].get("arguments") or "") + 50
    return n + 20


def fit_context(msgs, budget):
    """Shrink msgs in place to about `budget` chars. Keeps the system message
    and the latest user message; tool calls and their results stay paired."""
    total = sum(msg_chars(m) for m in msgs[1:])
    if total <= budget:
        return
    # 1. oldest tool results first: replace the text with a pointer
    for m in msgs[1:]:
        if total <= budget:
            return
        if m.get("role") == "tool" and len(m.get("content") or "") > 300 \
                and not m.get("_trimmed"):
            old = msg_chars(m)
            rid = save_result(m["content"])
            m["content"] = (f"[earlier result trimmed to save room: saved as result #{rid}, "
                            f"use read_saved_result if needed]")
            m["_trimmed"] = True
            total += msg_chars(m) - old
    # 2. then whole old turns (a user message up to the next one)
    users = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    dropped = 0
    while total > budget and len(users) > 1:
        cut = users[1] - users[0]
        total -= sum(msg_chars(m) for m in msgs[users[0]:users[1]])
        del msgs[users[0]:users[1]]
        users = [i - cut for i in users[1:]]
        dropped += 1
    if dropped:
        print(dim(f"  (dropped {dropped} oldest exchange(s) to fit the model's memory)"))


def clean(msgs):
    """Copy of msgs without pi-code's private keys."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in msgs]
