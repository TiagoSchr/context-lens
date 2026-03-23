"""
Retrieval policy per task type.
Defines which context levels to include and how much.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class TaskPolicy:
    task: str
    use_level0: bool = True
    use_level1: bool = True
    use_level2: bool = False
    use_level3: bool = False
    level1_limit: int = 150
    level2_files: int = 2
    level2_body_lines: int = 10
    level3_files: int = 1
    level3_max_lines: int = 200


POLICIES: dict[str, TaskPolicy] = {
    "explain": TaskPolicy(
        task="explain",
        use_level0=True,
        use_level1=True,
        use_level2=True,
        use_level3=False,
        level1_limit=60,   # reduzido: foca nos mais relevantes
        level2_files=3,
        level2_body_lines=8,
    ),
    "bugfix": TaskPolicy(
        task="bugfix",
        use_level0=False,
        use_level1=True,
        use_level2=True,
        use_level3=True,
        level1_limit=60,
        level2_files=4,
        level2_body_lines=15,
        level3_files=3,       # até 3 arquivos raw (era 2)
        level3_max_lines=250, # mais linhas por arquivo (era 150)
    ),
    "refactor": TaskPolicy(
        task="refactor",
        use_level0=False,
        use_level1=True,
        use_level2=True,
        use_level3=True,
        level1_limit=80,
        level2_files=2,
        level2_body_lines=12,
        level3_files=1,
        level3_max_lines=200,
    ),
    "generate_test": TaskPolicy(
        task="generate_test",
        use_level0=False,
        use_level1=True,
        use_level2=False,
        use_level3=True,
        level1_limit=50,
        level3_files=2,
        level3_max_lines=200,
    ),
    "navigate": TaskPolicy(
        task="navigate",
        use_level0=True,
        use_level1=True,
        use_level2=False,
        use_level3=False,
        level1_limit=60,  # só os mais relevantes — FTS já ranqueia por relevância
    ),
}
