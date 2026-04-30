from threading import Lock

class IDs:
    def __init__(self):
        self.ids = []
        self.next_id = 0
        self.lock = Lock()
    
    def acquire(self):
        self.lock.acquire()
        n = self.next_id    
        self.next_id = n + 1
        self.lock.release()

        self.ids.append(n)
        assert self.ids.count(n) == 1, "Could not assign ID"
        return n

tracker = IDs()

