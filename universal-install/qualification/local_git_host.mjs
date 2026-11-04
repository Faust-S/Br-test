import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {observeLocalGit} from "../host-bindings/local-git-source.mjs";
const observed=observeLocalGit(process.cwd());
const expected=execFileSync("git",["rev-parse","HEAD"],{encoding:"utf8"}).trim();
assert.equal(observed.status,"OK");
assert.equal(observed.headSha,expected);
assert.equal(observed.complete,false);
assert.equal(observed.headComplete,true);
assert.equal(observed.branchScope,"WORKTREE_ONLY");
assert.equal(observed.provenance.access,"local");
assert.equal(observed.readiness,"not_assessed");
assert.equal(observed.dirty,false);
const unavailable=observeLocalGit(process.cwd()+"/non-existent-repository-fixture");
assert.equal(unavailable.status,"SOURCE_UNAVAILABLE");
assert.equal(unavailable.complete,false);
console.log(JSON.stringify({schema:"IWXA_LOCAL_GIT_QUALIFICATION/1",
  status:"PASS",headSha:expected,dirty:false,local_read_only:true,
  missing_source:"SOURCE_UNAVAILABLE"}));
