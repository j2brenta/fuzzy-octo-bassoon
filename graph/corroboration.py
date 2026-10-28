"""Agreement topology and reviewer-checked primary-origin credit."""


def assertion_groups(claims, edges):
    parent = {cid:cid for cid in claims}
    def root(cid):
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid
    for e in edges:
        if e.type in {'SUPPORTS','DUPLICATES'} and e.src in parent and e.dst in parent:
            parent[root(e.src)] = root(e.dst)
    return {cid:root(cid) for cid in claims}


def checked_origins(claims, groups):
    origins = {}
    for cid,n in claims.items():
        if n.speaker_id and n.attribution_reviewed and n.evidence_origin:
            origins.setdefault(groups[cid],{}).setdefault(n.speaker_id,set()).add(n.evidence_origin)
    credit = {}
    for group,speakers in origins.items():
        for speaker,own in speakers.items():
            peers = set().union(*(urls for peer,urls in speakers.items() if peer != speaker))
            credit.setdefault(speaker,set()).update((group,url) for url in peers - own)
    return credit
