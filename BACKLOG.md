# Backlog

Unplanned ideas and future enhancements. Not committed to any phase.

## Format

| Status | Meaning |
|--------|---------|
| `new` | Just captured, not discussed |
| `discussed` | Talked through, awaiting decision |
| `accepted` | Ready to plan when capacity allows |
| `deferred` | Explicitly postponed |
| `rejected` | Decided not to do |

---

## Ideas

| Status | Date | Idea |
|--------|------|------|
| new | 2026-02-20 | 并行检查机制：判断potentially可以并行的task之间有没有资源竞争（比如通过检查是不是refer了同一个spec章节），进而优化调度 |
| new | 2026-02-20 | 锁机制：比如field test的时候不允许有其他并行，以防干扰测试 |
