#!/usr/bin/python

# This script can be run within cardoon environment:
#
#  cardoon -x profile_netlist.py <netlist file>
#
# It can also be run from the iterative shell:
#
#  cardoon -i
#  run -i profile_netlist.py <netlist file>

import pstats
import profile
import sys

if len(sys.argv) != 2:
    print('Usage: run -i profile_netlist.py <netlist file>')
else:
    analysisQueue = parse_net(sys.argv[1])
    profile.run('run_analyses(analysisQueue)','foo.out')
    p = pstats.Stats('foo.out')
    p.strip_dirs().sort_stats(-1)
    p.sort_stats('cumulative').print_stats(25)
