#!/usr/bin/env bash
set -euo pipefail
sha="$(git rev-parse HEAD)"
create=false
publish=false
if [[ "${GITHUB_EVENT_NAME:-}" == workflow_dispatch ]]; then
  [[ "$GITHUB_REF" == refs/heads/master ]] || { echo 'Run manual releases from master' >&2; exit 1; }
  tag="${RELEASE_TAG:-}"
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
  sha="$(git rev-parse --verify "refs/tags/$tag^{commit}")"
  git merge-base --is-ancestor "$sha" HEAD
  version="$(git show "$sha:VERSION")"
  test "$tag" = "v$version"
  test "$version" = "$(git show "$sha:RELEASE")"
  publish=true
else
  version="$(cat VERSION)"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
  test "$version" = "$(cat RELEASE)"
  tag="v$version"
  if [[ "$GITHUB_REF" == refs/tags/* ]]; then
    test "$GITHUB_REF" = "refs/tags/$tag"
    publish=true
  elif [[ "$GITHUB_REF" == refs/heads/master ]]; then
    if git show-ref --verify --quiet "refs/tags/$tag"; then
      tagged_sha="$(git rev-parse "refs/tags/$tag^{commit}")"
      git merge-base --is-ancestor "$tagged_sha" "$sha"
      if [[ "$tagged_sha" == "$sha" ]]; then publish=true; fi
    else
      create=true
      publish=true
    fi
  fi
fi
{
  echo "tag=$tag"
  echo "sha=$sha"
  echo "create=$create"
  echo "publish=$publish"
} >> "$GITHUB_OUTPUT"
