"""Online login throttle used by app/fixed.py: sliding-window counters per IP and per account."""
import time
from collections import defaultdict, deque


class LoginThrottle:
    def __init__(self, window_s=600, max_fail_ip=20, max_fail_user=10, max_users_ip=8, clock=time.monotonic):
        self.w, self.max_fail_ip, self.max_fail_user, self.max_users_ip = window_s, max_fail_ip, max_fail_user, max_users_ip
        self.clock = clock
        self.fail_ip = defaultdict(deque)       # ip -> fail timestamps
        self.fail_user = defaultdict(deque)     # user -> fail timestamps
        self.users_ip = defaultdict(deque)      # ip -> (ts, user) of failed attempts

    def _trim(self, dq, now):
        while dq and (dq[0][0] if isinstance(dq[0], tuple) else dq[0]) < now - self.w:
            dq.popleft()

    def blocked(self, ip, user) -> bool:
        now = self.clock()
        for dq in (self.fail_ip[ip], self.fail_user[user], self.users_ip[ip]):
            self._trim(dq, now)
        return (len(self.fail_ip[ip]) >= self.max_fail_ip or len(self.fail_user[user]) >= self.max_fail_user
                or len({u for _, u in self.users_ip[ip]}) >= self.max_users_ip)

    def record(self, ip, user, ok: bool):
        if ok:
            self.fail_user.pop(user, None)
            return
        now = self.clock()
        self.fail_ip[ip].append(now)
        self.fail_user[user].append(now)
        self.users_ip[ip].append((now, user))
