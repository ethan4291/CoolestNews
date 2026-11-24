#!/bin/bash
# Double-click this file in Finder to launch NewsHub.
cd "$(dirname "$0")"
(sleep 1.5 && open http://localhost:8777) &
python3 server.py
