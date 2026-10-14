# 08 Upgrading to a newer upstream release

**For:** whoever maintains the fork.

Moving the fork from one Apache Superset release to the next. This is a different
operation from "Migrating between modes" in [07](07-operations.md): that moves
*data* between stacks, this moves *our changes* onto a new upstream base.

## The branch model

One long-lived branch, with tags marking everything else:

```
lauretta-superset-main   the live line: upstream release <version> + our commits
base/<version>           upstream's <version> release commit, exactly. Never ours.
archive/<version>-fork   our line as it stood on <version>, frozen just before the
                         rebase onto the next release. The rollback point.
```

`archive/5.0.0-fork` is the exception: it is the pre-6.1 lineage, which cannot be
rebased or rolled back to — see "Why the 5.0 → 6.1 upgrade did not use this
procedure".

The branch name deliberately carries no version. A name like `current/<version>-fork`
asserts two things — "this is the live line" and "this is based on that release" —
and they stop agreeing the moment you upgrade. Renaming a default branch breaks every
clone, open PR, CI reference and doc link, so the stable thing gets the stable name
and the version lives on a tag, which is immutable and free.

`base/<version>` is the base argument to the rebase below, and the left-hand side of
every "what do we carry" diff:

```bash
git log --oneline base/<version>..lauretta-superset-main   # exactly our commits
git diff --stat base/<version> lauretta-superset-main       # exactly our changes
```

It must sit on upstream's commit and nowhere else. If it pointed at one of our
commits, the rebase would replay only what came after it and silently drop
everything before.

**Why not upstream's own tag name.** Upstream carries hundreds of tags — release
candidates, `airbnb_prod.*`, `superset-helm-chart-*`, `v2021.*` — and a careless
fetch imports all of them. Keeping upstream tags out of this repo entirely, and
naming the ones we depend on under our own prefix, means a `6.1.0` tag here can never
be confused with, or replaced by, upstream's.

`archive/<version>-fork`, made at the next upgrade, records the line's final state,
which is what rollback and comparison need. To mark which build a client runs, use
a separate release-tag scheme.

Only `base/*` and `archive/*` tags belong in this repo, locally and on origin.

## Why this is a rebase and not a merge

Upstream tags releases on **release branches cut from master**, not on master
itself. So release tags are not ancestors of one another, and a tag-to-tag merge
does not mean what it looks like:

```
merge-base(6.0.0, 6.1.0)         2025-08-18   the 6.0 branch point, not 6.0.0
commits on 6.0.0 not in 6.1.0           247   release-branch work: RC fixes,
                                              cherry-picks, changelog churn
commits on 6.1.0 not in 6.0.0          1886
```

`git merge <new>` would three-way against that branch point and treat those couple
of hundred release-branch commits as *our* side — commits nobody here wrote, many of
them cherry-picks of fixes that also reached master as different SHAs, which collide
as duplicate changes. Against that, our own divergence is a handful of commits over
~100 files. Rebasing replays only what we actually own.

It also keeps the branch at a shape worth having: *N commits on top of release
`base/X`*, so `git log base/X..HEAD` is always exactly our divergence and nothing else.

## Steps

Two placeholders throughout: `<old>` is the release you are currently on — the one
`base/<old>` names — and `<new>` is the release you are moving to.

Set this once per clone, so that no fetch from upstream can import its tags:

```bash
git config remote.upstream.tagOpt --no-tags
```

```bash
# 0. Confirm base/<old> is upstream's commit. The two hashes must match; if they
#    don't, stop and fix the tag before rebasing.
git rev-parse 'base/<old>^{commit}'
git ls-remote upstream 'refs/tags/<old>^{}' 'refs/tags/<old>'   # ^{} line if annotated

# 1. Fetch the new release and tag it under our prefix. Fetching the ref, rather
#    than `tag <new>`, leaves it in FETCH_HEAD instead of creating a local `<new>` tag.
git fetch --no-tags upstream 'refs/tags/<new>'
git tag base/<new> 'FETCH_HEAD^{commit}'
git push origin base/<new>

# 2. Preserve the current line before rewriting it. This is the rollback point:
#    after the force-push below, origin has no other copy of it.
git tag archive/<old>-fork lauretta-superset-main
git push origin archive/<old>-fork

# 3. Replay our commits onto the new release.
git rebase --onto base/<new> base/<old> lauretta-superset-main
```

Conflicts land only in files we modify. Resolve them against the *new* upstream
code rather than reapplying the old patch — upstream refactors, and a change that
was a one-line edit against `<old>` may need re-implementing against `<new>`. Where upstream has
since solved the problem a patch existed for, drop the patch instead of porting it.

Then verify before publishing. The frontend gates, in the order upstream CI runs
them, from clean build output:

```bash
cd superset-frontend
rm -rf packages/*/lib packages/*/esm plugins/*/lib plugins/*/esm
find packages plugins -name '*.tsbuildinfo' -delete
npm install && npm run plugins:build && npm run type && npm run build
```

The clean step is not optional. `tsc` resolves workspace packages through their
built `lib/*.d.ts`, so stale output from a previous build makes the type check
pass against the *old* declarations — a green run that proves nothing. Deleting
`lib/`/`esm/` without also deleting `.tsbuildinfo` is worse: tsc then believes the
projects are current, skips rebuilding them, and every plugin fails with `TS6305`
pointing at outputs that no longer exist.

Then boot each mode and work through
[09 Verification](09-verification-and-troubleshooting.md). Finally:

```bash
git push --force-with-lease origin lauretta-superset-main
```

`base/<new>` was tagged in step 1 and needs nothing further: it is upstream's
commit, which the finished rebase sits on. Confirm the new line has the right shape
— the log should list our commits and nothing else:

```bash
git log --oneline base/<new>..lauretta-superset-main
```

The force-push is inherent to rebasing. Tell anyone with a clone not to `git pull`.
With git's defaults (2.33 and later) the pull stops with "Need to specify how to
reconcile divergent branches", but a clone with `pull.rebase` or `pull.ff`
configured will instead try to merge or replay the old line onto the new one. The
fix is `git fetch && git reset --hard origin/lauretta-superset-main`, after saving
local work.

## The metadata database is a separate migration

Two independent things can change in one upstream release, and they want doing in
order, not together:

- **The Postgres major version.** Upstream's compose files pin a Postgres major
  version that changes between releases — `postgres:15` in 5.0, `16` in 6.0, `17` in
  6.1 — so check the `image: postgres:` lines in the new release. Postgres will not start on a data directory written by another major
  version — it fails with `database files are incompatible with server`, and there is
  no in-place upgrade. See "Upgrading the Postgres major version" in [07](07-operations.md).
- **Superset's own schema.** `superset-init` runs `superset db upgrade`, applying the
  Alembic chain for every release in between. Some of those migrations rewrite chart
  params rather than just moving columns.

Restore the old schema onto the new Postgres first, *then* let Superset migrate it.
Doing both at once leaves you unable to tell which half failed. Run the whole thing
against a restored copy before touching production.

Note that migrations only run against the metadata database. A dashboard ZIP
exported from an older release is imported as-is, so a chart carrying renamed form
data keeps the old keys and the new code simply ignores them.

## Why the 5.0 → 6.1 upgrade did not use this procedure

It could not. That fork descended from a lineage where Superset's 2017 history had
been rewritten, so it shared only a November 2017 merge-base with upstream — every
release tag was unreachable and no rebase or merge was possible. The fix was a
one-off replant: branch from upstream's 6.1.0 tag and re-apply the fork's ~90 files by
hand. `archive/5.0.0-fork` preserves that lineage; it is kept reachable but is not
mergeable and should not be branched from.

The branch this produced descends cleanly from `6.1.0`, so that cost is spent. From
here the procedure above applies.

## Keeping the next one cheap

The work scales with how much upstream code the fork edits in place, not with how
much code it adds. New files under `lauretta/`, `docker/pythonpath_secure/` and
`superset-frontend/custom_plugin/` cost nothing at upgrade time — they carry over
untouched. Edits to upstream files are what conflict.

The in-tree patches to `plugin-chart-echarts` are the expensive part for that
reason. Moving that work into `custom_plugin/` as a forked plugin would take the
conflict surface for it to zero.
