"""Evaluator-side helpers of the sample_intro, titration and electrochem acceptance suites: world calls by the trusted host and
checker helpers over the lab's own request log.  Nothing here is reachable from the sandbox."""


def world_call(lab, iid, cap, args, evaluator=True):
    """A trusted world call (the lab operator / the scene), asserted to succeed."""
    b = lab.backend
    rid = 'world-%s-%s-%d-%d' % (iid, cap, int(b.now() * 10), len(getattr(lab, '_world_calls', [])))
    lab.__dict__.setdefault('_world_calls', []).append(rid)
    r = b.evaluator_invoke(iid, cap, args, rid) if evaluator else b.invoke('chemlab_operator', iid, cap, args, rid)
    assert r.get('status') in ('succeeded', 'accepted'), (iid, cap, r)
    return r


def native_calls(c, iid, cap=None, ok_only=True):
    """The agent's native calls on ``iid`` (from the host-side request log), oldest first: [(capability, arguments, status)]."""
    out = []
    for e in c.lab.core.log.entries:
        for x in e.get('backend_calls') or []:
            if x.get('op') == 'invoke' and x.get('instrument') == iid and (cap is None or x.get('capability') == cap):
                if ok_only and x.get('status') not in ('succeeded', 'accepted'):
                    continue
                out.append((x.get('capability'), x.get('arguments') or {}, x.get('status')))
    return out


def last_args(c, iid, cap):
    calls = native_calls(c, iid, cap)
    return calls[-1][1] if calls else None


def jobs_of(c, iid):
    return [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


def used_job(c, iid):
    """A class job on ``iid`` that ended as succeeded."""
    js = jobs_of(c, iid)
    return c.add('a class job was used and it succeeded', any(j.state == 'succeeded' for j in js), [(j.handle, j.state) for j in js])


def text_has(c, name, fname, *words):
    t = c.work_file(fname) or ''
    return c.add(name, all(w in t for w in words), t[:120])
