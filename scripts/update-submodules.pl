#!/usr/bin/env perl
#
# Advance each submodule pointer to the head of its remote's default branch.
#
# This repo's pointers are bumped by hand and go stale, which makes a failing
# golden test easy to misread as a parser bug. See AGENTS.md.
#
# Deliberately not `git submodule update --remote`: that resolves the branch
# from submodule.<name>.branch in .gitmodules, which is unset here, and is
# further confused by leftover local branches inside the submodule checkouts.
# This asks each remote what its default branch is instead.
#
# Only the top-level submodules are touched. The nested hbt-data pointers
# belong to the implementations and are bumped in their own repos.
#
# Each submodule is also a flake input of the same name, locked in flake.lock,
# and .#bench builds the locked revision rather than the gitlink. So a run that
# advances a pointer re-locks that input too, which needs nix on PATH.
#
# Git is driven through Git.pm, the Perl API that ships with git. Its command
# methods throw a Git::Error::Command when git exits non-zero, so a failure
# this script does not catch aborts the run, as `set -e` did in the shell
# version.
#
# Usage:
#   scripts/update-submodules.pl [-n|--dry-run]

use v5.36;

use Git;

$| = 1;    # stdout is teed into the PR body; keep it in order with stderr

my $dry_run = 0;
if (@ARGV == 1 && ($ARGV[0] eq '-n' || $ARGV[0] eq '--dry-run')) {
    $dry_run = 1;
}
elsif (@ARGV) {
    print STDERR "usage: $0 [-n|--dry-run]\n";
    exit 2;
}

my $top = Git->repository();
chdir $top->wc_path() or die "cannot chdir to " . $top->wc_path() . ": $!\n";

# Run a git command, returning its output lines, or nothing if git exits
# non-zero. Only for commands whose failure is an answer rather than an error.
sub lines_or_nothing ($repo, @args) {
    my @out = eval { $repo->command(@args) };
    return $@ ? () : @out;
}

sub submodule_paths () {

    # --get-regexp exits 1 when nothing matches: no submodules, nothing to do.
    my $key = '^submodule\..*\.path$';
    my @records = lines_or_nothing($top, 'config', '--file', '.gitmodules', '--get-regexp', $key);
    return map { (split ' ', $_, 2)[1] } @records;
}

sub default_branch ($repo) {
    my @lines = lines_or_nothing($repo, 'ls-remote', '--symref', 'origin', 'HEAD');
    for (@lines) {
        return $1 if m{^ref: refs/heads/(\S+)\s+HEAD$};
    }
    return;
}

# In a shallow clone the counts are wrong rather than missing: the fetched
# head can arrive grafted, cut off from the old revision, so a one-commit
# fast-forward counts as +1 -1 and reads as a rewind. Ask whether the clone is
# shallow instead of trusting rev-list to fail.
sub describe_move ($repo, $old, $new) {
    if ($repo->command_oneline('rev-parse', '--is-shallow-repository') eq 'true') {
        return 'shallow clone, commits not counted';
    }
    my $ahead = $repo->command_oneline('rev-list', '--count', "$old..$new");
    my $behind = $repo->command_oneline('rev-list', '--count', "$new..$old");
    return $behind ? "+$ahead -$behind commits, NOT a fast-forward" : "+$ahead commits";
}

my $changed = 0;
my $failed = 0;
my @advanced;

for my $path (submodule_paths()) {
    my $repo = Git->repository(Directory => $path);
    my $old = $repo->command_oneline('rev-parse', 'HEAD');

    my $branch = default_branch($repo);
    if (!defined $branch) {
        print STDERR "$path: cannot determine default branch, skipped\n";
        $failed++;
        next;
    }

    $repo->command('fetch', '--quiet', 'origin', $branch);
    my $new = $repo->command_oneline('rev-parse', 'FETCH_HEAD');

    if ($old eq $new) {
        printf "%s: %s  already current (%s)\n", $path, substr($old, 0, 7), $branch;
        next;
    }

    printf "%s: %s -> %s  (%s, %s)\n", $path, substr($old, 0, 7), substr($new, 0, 7), $branch,
        describe_move($repo, $old, $new);

    if ($dry_run) {
        $changed++;
        next;
    }

    # A submodule left dirty by a local build fails to check out. Report and
    # skip it as the missing-branch case above does, rather than aborting the
    # run with earlier submodules already staged and no summary.
    if (!eval { $repo->command('checkout', '--quiet', '--detach', $new); 1 }) {
        print STDERR "$path: checkout failed, skipped (dirty worktree?)\n";
        $failed++;
        next;
    }
    $top->command('add', $path);
    push @advanced, $path;
    $changed++;
}

# Each implementation pins its own hbt-data revision. Advancing the outer
# pointer leaves those nested checkouts behind, which shows up as mass golden
# test failures rather than a clear error -- so bring them to what the new
# revisions pin. This checks out their pins; it does not advance them.
if ($changed && !$dry_run) {
    $top->command('submodule', 'update', '--init', '--recursive', '--quiet');

    # The same omission one layer up: a lock left on the old revisions builds
    # something other than what the gitlinks say. Only the inputs that moved:
    # a skipped submodule's worktree is in whatever state made it fail, which
    # is nothing this run should write into the lock.
    if (system('nix', 'flake', 'update', @advanced) != 0) {
        print STDERR "nix flake update failed\n";
        exit($? >> 8 || 1);
    }
    $top->command('add', 'flake.lock');
}

if (!$changed && !$failed) {
    print "all submodules current\n";
}
elsif (!$changed) {
    print "no submodules advanced\n";
}
elsif ($dry_run) {
    print "\n$changed submodule(s) would be advanced; re-run without --dry-run\n";
}
else {
    print "\n$changed submodule(s) advanced and staged, with flake.lock re-locked to match\n";
}

# Exit non-zero on a partial run so CI does not open a pull request that
# silently covers only some of the submodules.
if ($failed) {
    print STDERR "$failed submodule(s) could not be updated\n";
    exit 1;
}
