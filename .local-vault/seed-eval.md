# Seed evaluation

17/33 checks pass, 0 not scored.

| seed | check | expected | got | |
|---|---|---|---|---|
| demo-correction | kind | announcement | announcement | ✓ |
| demo-correction | durability | ephemeral | reference | ✗ |
| demo-correction | claims | 1 | 2 | ✗ |
| demo-correction | SUPERSEDES demo-original | True | False | ✗ |
| demo-repost | kind | announcement | announcement | ✓ |
| demo-repost | durability | ephemeral | reference | ✗ |
| demo-repost | claims | 1 | 1 | ✓ |
| demo-repost | DUPLICATES demo-original | True | False | ✗ |
| demo-original | kind | announcement | announcement | ✓ |
| demo-original | durability | ephemeral | ephemeral | ✓ |
| demo-original | claims | 1 | 1 | ✓ |
| demo-original | superseded | True | False | ✗ |
| people-list | author | None | None | ✓ |
| people-list | kind | post | article | ✗ |
| introspection-list | author | None | None | ✓ |
| introspection-list | kind | post | thought | ✗ |
| introspection-list | DUPLICATES people-list | True | False | ✗ |
| redis-talks | author | None | None | ✓ |
| redis-talks | kind | post | announcement | ✗ |
| redis-talks | people recall | 1.0 | 1.0 | ✓ |
| redis-talks | people precision | 1.0 | 1.0 | ✓ |
| economics-list | author | None | None | ✓ |
| economics-list | kind | post | thought | ✗ |
| eli5-post | author | Nikita Rvachev | Nikita Rvachev | ✓ |
| eli5-post | kind | post | thought | ✗ |
| eli5-post | durability | news | reference | ✗ |
| eli5-post | people recall | 1.0 | 1.0 | ✓ |
| eli5-post | people precision | 1.0 | 0.5 | ✗ |
| linkedin-math | author | Jianfeng Liu | Jianfeng Liu | ✓ |
| linkedin-math | kind | article | article | ✓ |
| linkedin-math | durability | reference | foundation | ✗ |
| linkedin-math | people recall | 1.0 | 1.0 | ✓ |
| linkedin-math | people precision | 1.0 | 0.0 | ✗ |
