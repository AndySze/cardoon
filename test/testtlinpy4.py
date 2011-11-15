"""
Test tlinpy4 model
"""
import numpy as np

parse_net('test.net')
ckt=cir.get_mainckt()
t1 = ckt.elemDict['tlinp4:tline1']
y1 = t1.get_ymatrix(1e9)
fvec=np.array([1e3, 2e5, 3e7, 2e10])
t1.nsect=3
t1.process_params(ckt)
