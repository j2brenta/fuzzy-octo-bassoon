# Seed evaluation

23/33 checks pass, 0 not scored.

| seed | check | expected | got | |
|---|---|---|---|---|
| demo-correction | kind | announcement | announcement | ✓ |
| demo-correction | durability | ephemeral | news | ✗ |
| demo-correction | claims | 1 | 1 | ✓ |
| demo-correction | SUPERSEDES demo-original | True | False | ✗ |
| demo-repost | kind | announcement | announcement | ✓ |
| demo-repost | durability | ephemeral | news | ✗ |
| demo-repost | claims | 1 | 1 | ✓ |
| demo-repost | DUPLICATES demo-original | True | True | ✓ |
| demo-original | kind | announcement | announcement | ✓ |
| demo-original | durability | ephemeral | news | ✗ |
| demo-original | claims | 1 | 1 | ✓ |
| demo-original | superseded | True | False | ✗ |
| people-list | author | None | None | ✓ |
| people-list | kind | post | post | ✓ |
| introspection-list | author | None | None | ✓ |
| introspection-list | kind | post | post | ✓ |
| introspection-list | DUPLICATES people-list | True | False | ✗ |
| redis-talks | author | None | None | ✓ |
| redis-talks | kind | post | newsletter_digest | ✗ |
| redis-talks | people recall | 1.0 | 1.0 | ✓ |
| redis-talks | people precision | 1.0 | 1.0 | ✓ |
| economics-list | author | None | None | ✓ |
| economics-list | kind | post | post | ✓ |
| eli5-post | author | Nikita Rvachev | Nikita Rvachev | ✓ |
| eli5-post | kind | post | post | ✓ |
| eli5-post | durability | news | reference | ✗ |
| eli5-post | people recall | 1.0 | 0.0 | ✗ |
| eli5-post | people precision | 1.0 | 1.0 | ✓ |
| linkedin-math | author | Jianfeng Liu | Jianfeng Liu | ✓ |
| linkedin-math | kind | article | post | ✗ |
| linkedin-math | durability | reference | reference | ✓ |
| linkedin-math | people recall | 1.0 | 1.0 | ✓ |
| linkedin-math | people precision | 1.0 | 1.0 | ✓ |
