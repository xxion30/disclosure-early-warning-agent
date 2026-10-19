"""api 테스트 도우미."""
import json

RCEPT = "20250401000001"  # 테스트용 형식만 맞춘 번호 (실제 공시 아님)


def login(client, email="p01@demo.local", password="demo1234"):
    return client.post("/auth/login", json={"email": email, "password": password})


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def read_events(client, headers, rcept_no=RCEPT):
    with client.stream("GET", f"/runs/{rcept_no}/events", headers=headers) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        return parse_sse(res.read().decode("utf-8"))
