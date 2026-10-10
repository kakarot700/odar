#!/bin/bash
cd "$(dirname "$0")"
for q in $(seq $1 $2); do PREFIX=$3 ./go2.sh $q $q $((8730+q)); SYS=$3 ./sc.sh $q $q; done
