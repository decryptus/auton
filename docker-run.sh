#!/bin/bash

AUTOND_ROOT="${AUTOND_ROOT:-"/etc/auton"}"
AUTOND_CONFFILE="${AUTOND_CONFFILE:-"${AUTOND_ROOT}/auton.yml"}"

mkdir -p "${AUTOND_ROOT}"

cd "${AUTOND_ROOT}"

if [[ ! -f "${AUTOND_CONFFILE}" ]] && [[ ! -z "${AUTOND_CONFIG}" ]];
then
    echo -e "${AUTOND_CONFIG}" > "${AUTOND_CONFFILE}"
fi

exec autond -f ${AUTOND_EXTRA_OPTS}
