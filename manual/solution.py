"""Задача:

Сложность: время O(?), память O(?)
"""


class Node:
    def __init__(self, key: int, value: int):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class LRUCache:
    def __init__(self, capacity: int):
        self._cap = capacity
        self._cache = {}

        self._first = Node(0, 0)
        self._last = Node(0, 0)
        self._first.next = self._last
        self._last.prev = self._first

    def _remove(self, node: Node):
        prev = node.prev
        next = node.next
        prev.next = next
        next.prev = prev

    def _insert_last(self, node: Node):
        prev = self._last.prev
        prev.next = node
        node.prev = prev
        node.next = self._last
        self._last.prev = node

    def get(self, key: int) -> int:
        node = self._cache.get(key)
        if not node:
            return -1
        
        self._remove(node)
        self._insert_last(node)

        return node.value

    def put(self, key: int, value: int) -> None:
        node = self._cache.get(key)
        if node:
            node.value = value
            self._remove(node)
            self._insert_last(node)
            return

        node = Node(key, value)
        self._insert_last(node)
        self._cache[key] = node

        if len(self._cache) > self._cap:
            node = self._first.next
            self._remove(node)
            del self._cache[node.key]

    @property
    def cap(self) -> int:
        return self._cap
        


def test_example():
    cache = LRUCache(2)
    cache.put(1, 1)
    cache.put(2, 2)
    assert cache.get(1) == 1
    cache.put(3, 3)
    assert cache.get(2) == -1
    cache.put(4, 4)
    assert cache.get(1) == -1
    assert cache.get(3) == 3
    assert cache.get(4) == 4


def test_update_value_and_recency():
    cache = LRUCache(2)
    cache.put(1, 1)
    cache.put(2, 2)
    cache.put(1, 10)
    cache.put(3, 3)
    assert cache.get(1) == 10
    assert cache.get(2) == -1


def test_capacity_one_and_miss():
    cache = LRUCache(1)
    assert cache.get(99) == -1
    cache.put(1, 1)
    cache.put(2, 2)
    assert cache.get(1) == -1
    assert cache.get(2) == 2

test_example()
test_update_value_and_recency()
test_capacity_one_and_miss()