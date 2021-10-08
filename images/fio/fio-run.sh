#!/usr/bin/env bash

#####
## Lightweight script that understands how to pass all the files from a
## directory as jobs for fio
#####

set -e

CONF_DIRECTORY="${CONF_DIRECTORY:-/fio}"
echo "[INFO] Conf directory - $CONF_DIRECTORY"
ls -lR $CONF_DIRECTORY

if [ -d "$CONF_DIRECTORY" ]; then
    echo "[INFO] Conf directory exists"
    JOB_FILES="$(find $CONF_DIRECTORY -mindepth 1 -maxdepth 1)"
fi
echo "[INFO] Job files - $JOB_FILES"

exec fio "$@" $JOB_FILES
