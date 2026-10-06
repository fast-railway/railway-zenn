import concurrent.futures
from datetime import datetime
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

# ---------------------------------------------------------
# Environment Parsers & Configuration
# ---------------------------------------------------------
def parse_range(var_name: str, default_min: float, default_max: float):
    raw_val = os.getenv(var_name, "").strip()
    if not raw_val:
        return default_min, default_max
    try:
        parts = [p.strip() for p in raw_val.split(",") if p.strip()]
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
        elif len(parts) == 1:
            val = float(parts[0])
            return val, val
    except ValueError:
        print(f"[WARN] Invalid range in '{var_name}' ('{raw_val}'). Using defaults ({default_min}, {default_max}).")
    return default_min, default_max


def parse_referrers(var_name: str, defaults: list):
    raw_val = os.getenv(var_name, "").strip()
    if not raw_val:
        return defaults
    items = [item.strip() for item in raw_val.split(",") if item.strip()]
    return items if items else defaults


# ZenRows Credentials & Bot Behavior
RAW_KEYS = os.getenv("ZENROWS_API_KEYS", "DefaultKey:ad7ebff1a2f2551662b95fb805411b26e725e03f")
WORKER_MIN, WORKER_MAX = parse_range("WORKER_COUNT_RANGE", 6, 8)
GAP_MIN, GAP_MAX = parse_range("WORKER_GAP_RANGE", 5.0, 9.0)
CYCLE_MIN, CYCLE_MAX = parse_range("CYCLE_INTERVAL_RANGE", 55.0, 70.0)

# Browser Rendering Toggle (ZenRows uses 'js_render')
BROWSER_RENDERING = os.getenv("BROWSER_RENDERING", "true").strip().lower()

# Default Referrers (Google, Facebook, and Internal app links)
DEFAULT_REFERRERS = [
    "https://app.bullpen.fi/",
    "https://www.google.com/",
    "https://www.facebook.com/"
]

# Parsed Referrer Pool (Custom comma-separated list via env or defaults)
REFERRERS = parse_referrers("REFERRERS", DEFAULT_REFERRERS)

# Target slugs
SLUGS = [
    "jack", "6DNUvqf", "652HU1t", "tzlMgCf", "fNPZlqT",
    "bTi9oJs", "QMOvAAL", "OVMrJe2", "VQH8P3L", "xDVN1Bq", "CLfcNh1"
]

# Supported ZenRows Geographic Tiers (ISO 2-letter codes)
TIER_1 = [
    ("FR", "fr"), ("DE", "de"), ("NL", "nl"), ("ES", "es"),
    ("IT", "it"), ("PL", "pl"), ("SE", "se"), ("BR", "br"),
    ("KR", "kr"), ("TR", "tr"), ("VN", "vn"), ("ID", "id"),
    ("CA", "ca"), ("JP", "jp"), ("SG", "sg"), ("AU", "au")
]
TIER_2 = [
    ("US", "us"), ("GB", "gb"), ("CZ", "cz"), ("RO", "ro"),
    ("AE", "ae"), ("MX", "mx"), ("TH", "th"), ("PH", "ph")
]
TIER_3 = [
    ("IN", "in"), ("SA", "sa"), ("HK", "hk"), ("TW", "tw"),
    ("ZA", "za"), ("AR", "ar"), ("CL", "cl"), ("IL", "il")
]


# ---------------------------------------------------------
# Key Structure & Manager
# ---------------------------------------------------------
class ManagedKey:
    def __init__(self, name: str, token: str):
        self.name = name.strip()
        self.token = token.strip()
        if len(self.token) >= 8:
            self.masked = f"{self.token[:4]}...{self.token[-4:]}"
        else:
            self.masked = self.token
        self.tag = f"{self.name} [{self.masked}]"


class KeyPoolManager:
    def __init__(self, raw_str: str):
        self.active_keys = []
        self.dead_keys = []
        self.index = 0

        entries = [k.strip() for k in raw_str.split(",") if k.strip()]
        for idx, entry in enumerate(entries, start=1):
            if ":" in entry:
                name, token = entry.split(":", 1)
                self.active_keys.append(ManagedKey(name, token))
            else:
                self.active_keys.append(ManagedKey(f"Key#{idx}", entry))

    def get_key(self) -> ManagedKey:
        if not self.active_keys:
            return None
        key = self.active_keys[self.index % len(self.active_keys)]
        self.index = (self.index + 1) % len(self.active_keys)
        return key

    def mark_dead(self, key_obj: ManagedKey, reason: str):
        if key_obj in self.active_keys:
            self.active_keys.remove(key_obj)
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            self.dead_keys.append((key_obj, reason, ts))

            print("\n" + "#" * 70)
            print(" [PINNED ALERT] API KEY DIED / EXHAUSTED CREDITS")
            print(f"  Key Identifier : {key_obj.tag}")
            print(f"  Death Time     : {ts}")
            print(f"  Confirmed Cause: {reason}")
            print(f"  Active Remaining: {len(self.active_keys)} key(s)")
            print("#" * 70 + "\n")

    def print_pinned_status(self):
        if not self.dead_keys:
            return
        print("-" * 70)
        print(" [PINNED AUDIT] PERMANENTLY DEAD KEYS:")
        for k_obj, reason, ts in self.dead_keys:
            print(f"  -> {k_obj.tag} | Died: {ts} | Reason: {reason}")
        print("-" * 70)


pool = KeyPoolManager(RAW_KEYS)


# ---------------------------------------------------------
# Dynamic Links & Routing
# ---------------------------------------------------------
def generate_cycle_links(worker_count: int):
    selected_slugs = random.sample(SLUGS, min(worker_count, len(SLUGS)))
    tasks = []
    for slug in selected_slugs:
        if random.random() < 0.86:
            url = f"https://app.bullpen.fi?via={slug}"
            ltype = "VIA (?)"
        else:
            url = f"https://go.bullpen.fi/{slug}"
            ltype = "DIRECT (/)"
        tasks.append((url, slug, ltype))
    return tasks


def pick_country():
    roll = random.random()
    if roll < 0.50:
        return "T1", *random.choice(TIER_1)
    elif roll < 0.85:
        return "T2", *random.choice(TIER_2)
    else:
        return "T3", *random.choice(TIER_3)


# ---------------------------------------------------------
# Worker Bot Task (ZenRows Integration)
# ---------------------------------------------------------
def execute_bot(bot_id: int, total_bots: int, target_url: str, slug: str, ltype: str, stagger_delay: float):
    time.sleep(stagger_delay)

    key_obj = pool.get_key()
    if not key_obj:
        return

    tier, label, code = pick_country()
    
    # ZenRows Parameters
    # - apikey: authentication token
    # - url: target target destination
    # - js_render: activates JS rendering (true/false)
    # - premium_proxy: true (enables residential IP routing required for proxy_country)
    # - proxy_country: 2-letter ISO code
    params = {
        "apikey": key_obj.token,
        "url": target_url,
        "js_render": "true" if BROWSER_RENDERING == "true" else "false",
        "premium_proxy": "true",
        "proxy_country": code
    }

    url = f"https://api.zenrows.com/v1/?{urllib.parse.urlencode(params)}"
    
    chosen_referrer = random.choice(REFERRERS)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": chosen_referrer
    }

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=65) as resp:
            print(f"[Bot-{bot_id}/{total_bots}] [{tier}-{label}] [{ltype} {slug}] [Ref: {chosen_referrer}] [{key_obj.tag}] -> HTTP {resp.status} OK")

    except urllib.error.HTTPError as e:
        raw_detail = e.read().decode("utf-8", errors="ignore")[:70].strip()

        # ZenRows 401: Invalid API key | 402/403: Out of credits or forbidden
        if e.code in (401, 402, 403):
            reason_msg = f"HTTP {e.code} Credits Exhausted / Invalid Token ({raw_detail})"
            pool.mark_dead(key_obj, reason_msg)
        elif e.code == 429:
            print(f"[Bot-{bot_id}] [{key_obj.tag}] [TRANSIENT] HTTP 429 Concurrency/Rate limit reached: {raw_detail}")
        elif e.code == 500:
            print(f"[Bot-{bot_id}] [{key_obj.tag}] [TRANSIENT] HTTP 500 ZenRows Proxy Timeout: {raw_detail}")
        elif e.code == 502:
            print(f"[Bot-{bot_id}] [{key_obj.tag}] [TRANSIENT] HTTP 502 Bad Gateway / Target Unresponsive: {raw_detail}")
        else:
            print(f"[Bot-{bot_id}] [{key_obj.tag}] [WARNING] HTTP {e.code}: {raw_detail}")

    except Exception as ex:
        print(f"[Bot-{bot_id}] [{key_obj.tag}] [CLIENT ERROR]: {str(ex)}")

    time.sleep(random.uniform(GAP_MIN, GAP_MAX))


# ---------------------------------------------------------
# Engine Main Loop
# ---------------------------------------------------------
def main():
    print("==================================================")
    print("      ZENROWS BOT ENGINE INITIALIZED              ")
    print("==================================================")
    print(f"Total Active Keys    : {len(pool.active_keys)}")
    print(f"Browser Rendering    : {BROWSER_RENDERING}")
    print(f"Configured Referrers : {len(REFERRERS)}")
    print(f"Workers Per Cycle    : {int(WORKER_MIN)} - {int(WORKER_MAX)}")
    print(f"Worker Gap Range     : {GAP_MIN:.1f}s - {GAP_MAX:.1f}s")
    print(f"Cycle Duration Range : {CYCLE_MIN:.1f}s - {CYCLE_MAX:.1f}s")
    print("==================================================\n")

    cycle_num = 1

    try:
        while True:
            if not pool.active_keys:
                print("\n" + "!" * 70)
                print(" [SHUTDOWN] ALL CONFIGURED KEYS ARE COMPLETELY DEAD / EXHAUSTED.")
                pool.print_pinned_status()
                print(" Process exiting now. Update ZENROWS_API_KEYS to resume.")
                print("!" * 70 + "\n")
                sys.exit(0)

            cycle_start = time.time()
            worker_count = random.randint(int(WORKER_MIN), int(WORKER_MAX))
            target_cycle_time = random.uniform(CYCLE_MIN, CYCLE_MAX)

            print(f"\n--- [Cycle #{cycle_num}] Starting {worker_count} bots | Target: {target_cycle_time:.1f}s | Active Keys: {len(pool.active_keys)} ---")

            tasks = generate_cycle_links(worker_count)

            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = []
                for idx, (url, slug, ltype) in enumerate(tasks):
                    stagger = idx * random.uniform(GAP_MIN, GAP_MAX)
                    futures.append(
                        executor.submit(execute_bot, idx + 1, worker_count, url, slug, ltype, stagger)
                    )
                concurrent.futures.wait(futures)

            elapsed = time.time() - cycle_start
            wait_time = target_cycle_time - elapsed

            pool.print_pinned_status()

            if wait_time > 0 and pool.active_keys:
                print(f"--- [Cycle #{cycle_num} Complete] Elapsed: {elapsed:.1f}s | Pausing {wait_time:.1f}s before next round ---")
                time.sleep(wait_time)
            elif pool.active_keys:
                print(f"--- [Cycle #{cycle_num} Complete] Elapsed: {elapsed:.1f}s | Starting next round immediately ---")

            cycle_num += 1

    except KeyboardInterrupt:
        print("\nTermination signal received. Exiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
