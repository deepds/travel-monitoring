import time, httpx
url = "https://ticket.rzd.ru/api/v1/suggests"
for label, to in (("Timeout(5.0)", httpx.Timeout(5.0)),
                  ("Timeout(10, read=8)", httpx.Timeout(10.0, read=8.0))):
    t = time.perf_counter()
    try:
        with httpx.Client(timeout=to) as c:
            c.get(url, params={"Query": "Москва", "Language": "ru"})
        print(f"{label}: OK за {time.perf_counter()-t:.1f}s")
    except Exception as e:
        print(f"{label}: {type(e).__name__} за {time.perf_counter()-t:.1f}s")
