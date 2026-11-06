#!/usr/bin/env bash
set -euo pipefail
sha="$(git rev-parse HEAD)"
create=false
publish=false
prerelease=false
stable_pattern='^[0-9]+\.[0-9]+\.[0-9]+$'
candidate_pattern='^[0-9]+\.[0-9]+\.[0-9]+rc[1-9][0-9]*$'

if [[ "${GITHUB_EVENT_NAME:-}" == workflow_dispatch ]]; then
  tag="${RELEASE_TAG:-}"
  [[ "$tag" == v* ]] || exit 1
  version="${tag#v}"
  [[ "$version" =~ $stable_pattern || "$version" =~ $candidate_pattern ]] || exit 1
  sha="$(git rev-parse --verify "refs/tags/$tag^{commit}")"
  git merge-base --is-ancestor "$sha" HEAD
  test "$version" = "$(git show "$sha:VERSION")"
  test "$version" = "$(git show "$sha:RELEASE")"
else
  version="$(cat VERSION)"
  [[ "$version" =~ $stable_pattern || "$version" =~ $candidate_pattern ]] || exit 1
  test "$version" = "$(cat RELEASE)"
  tag="v$version"
fi
if [[ "$version" =~ $candidate_pattern ]]; then prerelease=true; fi

if [[ "${GITHUB_EVENT_NAME:-}" == pull_request ]]; then
  : # Validate/build only. A pull request never creates tags or publishes.
elif [[ "$GITHUB_REF" == refs/tags/* ]]; then
  test "$GITHUB_REF" = "refs/tags/$tag"
  if [[ "$prerelease" == true ]]; then
    series=refs/remotes/origin/release/2.0
    git show-ref --verify --quiet "$series" || series=refs/heads/release/2.0
    git merge-base --is-ancestor "$sha" "$series"
  fi
  publish=true
elif [[ "$GITHUB_REF" == refs/heads/master || "$GITHUB_REF" == refs/heads/release/2.0 ]]; then
  if [[ "$GITHUB_REF" == refs/heads/master ]]; then
    [[ "$prerelease" == false ]] || { echo 'Candidates must publish from release/2.0' >&2; exit 1; }
  else
    [[ "$prerelease" == true ]] || { echo 'Stable publication requires master' >&2; exit 1; }
  fi
  if [[ "${GITHUB_EVENT_NAME:-}" == workflow_dispatch ]]; then
    publish=true
  elif git show-ref --verify --quiet "refs/tags/$tag"; then
    tagged_sha="$(git rev-parse "refs/tags/$tag^{commit}")"
    git merge-base --is-ancestor "$tagged_sha" "$sha"
    if [[ "$tagged_sha" == "$sha" ]]; then publish=true; fi
  else
    create=true
    publish=true
  fi
elif [[ "${GITHUB_EVENT_NAME:-}" == workflow_dispatch ]]; then
  echo 'Manual publication requires the matching release branch' >&2
  exit 1
fi
{
  echo "tag=$tag"
  echo "sha=$sha"
  echo "create=$create"
  echo "publish=$publish"
  echo "prerelease=$prerelease"
} >> "$GITHUB_OUTPUT"
