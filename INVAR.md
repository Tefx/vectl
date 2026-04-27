# Invar Protocol

## Before Writing Code

1. If you are writing a Core function, write `@pre`, `@post`, and at least one doctest BEFORE implementation.
2. If you are unsure whether code belongs in Core or Shell, use Shell.
3. Run `invar guard` after changes. Fix errors before committing.

## Core vs Shell

Use Shell if the code does any of these:
- reads or writes files
- makes network requests
- reads environment variables
- uses current time or randomness without injection
- performs subprocess or system I/O

Use Core for pure logic that only transforms already-available data.

| Zone | Path | Rules |
|------|------|-------|
| Core | `**/core/**` | `@pre` + `@post` + doctest, no I/O imports |
| Shell | `**/shell/**` | returns `Result[T, E]`, performs I/O |

## Contract Syntax Traps

### `@pre` lambda must include all function parameters

```python
# WRONG
@pre(lambda x: x >= 0)
def calc(x: int, y: int = 0): ...

# CORRECT
@pre(lambda x, y=0: x >= 0)
def calc(x: int, y: int = 0): ...
```

### `@post` only receives `result`

```python
# WRONG
@post(lambda result: result > x)

# CORRECT
@post(lambda result: result >= 0)
```

### Contracts must be semantic, not just type checks

```python
# WEAK
@pre(lambda x: isinstance(x, int))

# BETTER
@pre(lambda x: x > 0)
@pre(lambda start, end: start < end)
```

## Canonical Core Example

```python
from invar_runtime import pre, post

@pre(lambda price, discount: price > 0 and 0 <= discount <= 1)
@post(lambda result: result >= 0)
def discounted_price(price: float, discount: float) -> float:
    """
    >>> discounted_price(100, 0.2)
    80.0
    """
    return price * (1 - discount)
```

## Canonical Shell Example

```python
from pathlib import Path
from returns.result import Result, Success, Failure

def read_config(path: Path) -> Result[str, str]:
    try:
        return Success(path.read_text())
    except OSError as exc:
        return Failure(str(exc))
```

## Escape Hatches

```python
# @invar:allow shell_complexity: orchestration requires many steps
# @invar:allow shell_complexity: orchestration requires many steps
```

Use escape hatches rarely and always include a reason.

## Minimal Configuration

```toml
[tool.invar.guard]
core_paths = ["src/myapp/core"]
shell_paths = ["src/myapp/shell"]
```

## Common Guard Repairs

| Error | Fix |
|------|-----|
| `missing_contract` | add `@pre`, `@post`, and a doctest before implementation |
| `param_mismatch` | include every function parameter in the `@pre` lambda |
| `shell_result` | return `Result[T, E]` from Shell functions |
| `forbidden_import` | move I/O out of Core or inject the value as a parameter |
