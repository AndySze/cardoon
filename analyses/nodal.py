"""
:mod:`nodal` -- Nodal Analysis
------------------------------

.. module:: nodal
.. moduleauthor:: Carlos Christoffersen

This module contains basic classes/functions for nodal analysis. These
are part of the standard netlist analyses but they can also be used
independently.

"""

import numpy as np
from fsolve import fsolve_Newton, NoConvergenceError
from integration import BEuler

# ****************** Stand-alone functions to be optimized ****************

def set_quad(G, row1, col1, row2, col2, g):
    """
    Set transconductance/transcapacitance quad

    G: target matrix
    """
    # good cython candidate
    #import pdb; pdb.set_trace()
    if col1 >= 0:
        if row1 >= 0:
            G[row1, col1] += g
        if row2 >= 0:
            G[row2, col1] -= g
    if col2 >= 0:
        if row1 >= 0:
            G[row1, col2] -= g
        if row2 >= 0:
            G[row2, col2] += g


def set_xin(xin, posCols, negCols, xVec):
    """
    Calculate input port voltage xin
    """
    # good candidate for cython
    for i,j in posCols:
        xin[i] += xVec[j]
    for i,j in negCols:
        xin[i] -= xVec[j]


def set_i(iVec, posRows, negRows, current):
    """
    Set current contributions of current into iVec
    """
    # good candidate for cython
    for i,j in posRows:
        iVec[j] += current[i]
    for i,j in negRows:
        iVec[j] -= current[i]


def set_Jac(M, posRows, negRows, posCols, negCols, Jac):
    """
    Set current contributions of Jac into M
    """
    # good candidate for cython
    for i1, i in posRows:
        for j1, j in posCols:
            M[i,j] += Jac[i1,j1]
    for i1, i in negRows:
        for j1, j in negCols:
            M[i,j] += Jac[i1,j1]
    for i1, i in posRows:
        for j1, j in negCols:
            M[i,j] -= Jac[i1,j1]
    for i1, i in negRows:
        for j1, j in posCols:
            M[i,j] -= Jac[i1,j1]


# ********************** Regular functions *****************************

def make_nodal_circuit(ckt, reference='gnd'):
    """
    Add attributes to Circuit/Elements/Terminals for nodal analysis

    This functionality should be useful for any kind of nodal-based
    analysis (DC, AC, TRAN, HB, etc.)

    Takes a Circuit instance (ckt) as an argument. If the circuit
    contains the 'gnd' node, it is used as the reference. Otherwise a
    reference node must be indicated.

    New attributes are added in Circuit/Element/Terminal
    instances. All new attributes start with ``nD_``

    Works with subcircuits too (see ``nD_namRClist`` attribute)
    """
    # get ground node
    ckt.nD_ref = ckt.get_term(reference)

    # make a list of all non-reference terminals in circuit 
    ckt.nD_termList = ckt.termDict.values() + ckt.get_internal_terms()
    # remove ground node from terminal list
    ckt.nD_termList.remove(ckt.nD_ref)
    # Assign a number (0-inf) to all nodes. For reference nodes
    # assign -1 
    ckt.nD_ref.nD_namRC = -1
    # Make a list of all elements
    ckt.nD_elemList = ckt.elemDict.values()
    # Set RC number of reference terminals to -1
    for elem in ckt.nD_elemList:
        if elem.localReference:
            elem.neighbour[elem.localReference].nD_namRC = -1
    # For the future: use graph techniques to find the optimum
    # terminal order
    for i, term in enumerate(ckt.nD_termList):
        term.nD_namRC = i

    # Store internal RC numbers for later use
    for elem in ckt.nD_elemList:
        elem.nD_intRC = [term.nD_namRC for term in 
                         elem.neighbour[elem.numTerms:]]

    # Dimension is the number of unknowns to solve for
    ckt.nD_dimension = len(ckt.nD_termList)
    # Number of external terminals excluding reference
    ckt.nD_nterms = len(ckt.termDict.values()) - 1

    # Create specialized element lists
    ckt.nD_nlinElem = filter(lambda x: x.isNonlinear, ckt.nD_elemList)
    ckt.nD_freqDefinedElem = filter(lambda x: x.isFreqDefined, ckt.nD_elemList)
    ckt.nD_sourceDCElem = filter(lambda x: x.isDCSource, ckt.nD_elemList)
    ckt.nD_sourceTDElem = filter(lambda x: x.isTDSource, ckt.nD_elemList)
    ckt.nD_sourceFDElem = filter(lambda x: x.isFDSource, ckt.nD_elemList)

    # Map row/column numbers directly into VC*S descriptions
    for elem in ckt.nD_elemList:
        process_nodal_element(elem)

    # Subcircuit-connection processing
    try:
        connectTerms = ckt.get_connections()
        # List of RC numbers of external connections
        ckt.nD_namRClist = [term.nD_namRC for term in connectTerms]
    except AttributeError:
        # Not a subcircuit
        pass

def restore_RCnumbers(elem):
    """
    Restore RC numbers in internal terminals

    Assumption is number of internal terminals is the same
    """
    for term, i in zip(elem.neighbour[elem.numTerms:], elem.nD_intRC):
        term.nD_namRC = i

def process_nodal_element(elem):
    """
    Process element for nodal analysis
    """
    # Create list with RC numbers (choose one)
    # rcList = map(lambda x: x.nD_namRC, elem.neighbour)
    rcList = [x.nD_namRC for x in elem.neighbour]

    # Translate linear VCCS/VCQS format
    def convert_vcs(x):
        """
        Converts format of VC*S 

        input: [(contn1, contn2), (outn1, outn2), g]

        output: [row1, col1, row2, col2, g]
        """
        col1 = rcList[x[0][0]]
        col2 = rcList[x[0][1]]
        row1 = rcList[x[1][0]]
        row2 = rcList[x[1][1]]
        return [row1, col1, row2, col2, x[2]]
    elem.nD_linVCCS = map(convert_vcs, elem.linearVCCS)
    elem.nD_linVCQS = map(convert_vcs, elem.linearVCQS)

    # Translate positive and negative terminal numbers
    def create_list(portlist):
        """
        Converts an internal port list into 2 lists with (+-) nodes

        The format of each list is::

            (internal term number, namRC number)
        """
        tmp0 = [rcList[x1[0]] for x1 in portlist]
        tmp1 = [rcList[x1[1]] for x1 in portlist]
        return ([(i, j) for i,j in enumerate(tmp0) if j > -1],
                [(i, j) for i,j in enumerate(tmp1) if j > -1])

    # Convert nonlinear device descriptions to a format more
    # readily usable for the NA approach
    if elem.isNonlinear:
        # Control voltages
        (elem.nD_vpos, elem.nD_vneg) = create_list(elem.controlPorts)
        # Current source terminals
        (elem.nD_cpos, elem.nD_cneg) = create_list(elem.csOutPorts)
        # Charge source terminals
        (elem.nD_qpos, elem.nD_qneg) = create_list(elem.qsOutPorts)
# Disabled since not needed for now
#            # Create list with external ports for gmin calculation
#            nports = elem.numTerms - 1
#            elem.nD_extPorts = [(i, nports) for i in range(nports)]
#            (elem.nD_epos, elem.nD_eneg) = create_list(elem.nD_extPorts)

    # Convert frequency-defined elements
    if elem.isFreqDefined:
        (elem.nD_fpos, elem.nD_fneg) =  create_list(elem.fPortsDefinition)

    # Translate source output terms
    if elem.isDCSource or elem.isTDSource or elem.isFDSource:
        # first get the destination row/columns 
        n1 = rcList[elem.sourceOutput[0]]
        n2 = rcList[elem.sourceOutput[1]]
        elem.nD_sourceOut = (n1, n2)

#----------------------------------------------------------------------    

def run_AC(ckt, fvec):
    """
    Set up and solve AC equations

    ckt: nodal-ready Circuit instance (ckt) instance with OP already set

    fvec: frequency vector. All frequencies must be different from zero

    DC solution not calculated here as it needs special treatment and
    that solution is already obtained by the OP analysis.

    Set results in terminals in vectors named ``aC_V``

    Returns a matrix with results. Dimension: (nvars x nfreq)

    """
    # Number of frequencies
    nfreq = np.shape(fvec)[0]
    # Allocate matrices/vectors. 
    # G is used for G + dI/dv
    G = np.zeros((ckt.nD_dimension, ckt.nD_dimension))
    # C is used for C + dQ/dv
    C = np.zeros((ckt.nD_dimension, ckt.nD_dimension))

    # Linear contributions G and C in AC formulation
    for elem in ckt.nD_elemList:
        # All elements have nD_linVCCS (perhaps empty)
        for vccs in elem.nD_linVCCS:
            set_quad(G, *vccs)
        # All elements have nD_linVCQS (perhaps empty)
        for vcqs in elem.nD_linVCQS:
            set_quad(C, *vcqs)
    # Calculate dI/dx and dQ/dx and add to G and C
    for elem in ckt.nD_nlinElem:
        (outV, outJac) = elem.eval_and_deriv(elem.nD_xOP)
        set_Jac(G, elem.nD_cpos, elem.nD_cneg, 
                elem.nD_vpos, elem.nD_vneg, outJac)
        if len(elem.qsOutPorts):
            # import pdb; pdb.set_trace()
            # Get charge derivatives from outJac
            qJac = outJac[len(elem.csOutPorts):,:]
            set_Jac(C, elem.nD_qpos, elem.nD_qneg, 
                    elem.nD_vpos, elem.nD_vneg, qJac)

    # Frequency-dependent matrices: a matrix of complex vectors, each
    # vector conatins the values for all frequencies. This format is
    # compatible with the format returned by elem.get_Y_matrix(fvec)
    Y = np.zeros((ckt.nD_dimension, ckt.nD_dimension, nfreq), dtype=complex)
    # Frequency-defined elements
    for elem in ckt.nD_freqDefinedElem:
        # get first Y matrix for all frequencies. 
        set_Jac(Y, elem.nD_fpos, elem.nD_fneg, 
                elem.nD_fpos, elem.nD_fneg, elem.get_Y_matrix(fvec))

    # Sources
    sVec = np.zeros(ckt.nD_dimension, dtype = complex)
    for source in ckt.nD_sourceFDElem:
        try:
            current = source.get_AC()
            outTerm = source.nD_sourceOut
            # This may not need optimization because we usually do not
            # have too many independent sources
            if outTerm[0] >= 0:
                sVec[outTerm[0]] -= current
            if outTerm[1] >= 0:
                sVec[outTerm[1]] += current
        except AttributeError:
            # AC routine not implemented, ignore
            pass

#    if hasattr(ckt, 'nD_namRClist'):
#        # Allocate external currents vector
#        extSVec = np.zeros(ckt.nD_dimension)

    # Loop for each frequency: create and solve linear system
    j = complex(0., 1.)
    omegaVec = 2. * np.pi * fvec
    xVec = np.zeros((ckt.nD_dimension, nfreq), dtype=complex)
    for k, omega in enumerate(omegaVec):
        N = G + j * omega * C + Y[:,:,k] 
        xVec[:,k] = np.linalg.solve(N, sVec)
        
    # Save results in circuit
    # Set nodal voltage of reference to zero
    ckt.nD_ref.aC_V = 0.
    for k,term in enumerate(ckt.nD_termList):
        term.aC_V = xVec[k, :]

    return xVec

#-------------------------------------------------------------------------
# ****************************** Classes *********************************
#-------------------------------------------------------------------------

class _NLFunction:
    """
    Nonlinear function interface class

    This is an abstract class to be use as a base class by other
    nodal-based classes such as DCNodal
    """

    def __init__(self):
        # List here the functions that can be used to solve equations
        self.convergence_helpers = [self.solve_simple, 
                                    self.solve_homotopy_gmin, 
                                    self.solve_homotopy_source, 
                                    None]

    # The following functions used to solve equations, originally from
    # pycircuit
    def solve_simple(self, x0, sV):
        #"""Simple Newton's method"""
        # Docstring removed to avoid printing this all the time
        def f_Jac_eval(x):
            (iVec, Jac) = self.get_i_Jac(x) 
            return (iVec - sV, Jac)
    
        def f_eval(x):
            iVec = self.get_i(x) 
            return iVec - sV
    
        return fsolve_Newton(x0, f_Jac_eval, f_eval)
    

    def solve_homotopy_gmin(self, x0, sV):
        """Newton's method with gmin stepping"""
        x = np.copy(x0)
        totIter = 0
        idx = np.arange(self.ckt.nD_nterms)
        for gminexp in np.arange(-1., -8., -1):
            gmin = 10.**gminexp
            # Add gmin from ground to all external nodes. Assumes all
            # external nodes are sorted first in the vector. This will
            # not work if the terminal order is changed.
            def f_Jac_eval(xvec):
                (iVec, Jac) = self.get_i_Jac(xvec) 
                iVec[idx] += gmin * xvec[idx]
                Jac[idx, idx] += gmin
                return (iVec - sV, Jac)
            def f_eval(xvec):
                iVec = self.get_i(xvec)
                iVec[idx] += gmin * xvec[idx]
                return iVec - sV
            (x, res, iterations) = fsolve_Newton(x0, f_Jac_eval, f_eval)
            print('gmin = {0}, res = {1}, iter = {2}'.format(
                    gmin, res, iterations))
            totIter += iterations

        # Call solve_simple with better initial guess
        (x, res, iterations) = self.solve_simple(x, sV)
        print('gmin = 0, res = {0}, iter = {1}'.format(res, iterations))
        totIter += iterations

        return (x, res, totIter)

# Old code for gmin homotopy: add conductances in parallel to
# nonlinear device external ports
#
#        for elem in self.ckt.nD_nlinElem:
#            Gadd = np.eye(len(elem.nD_extPorts))
#            set_Jac(Ggmin, elem.nD_epos, elem.nD_eneg, 
#                    elem.nD_epos, elem.nD_eneg, Gadd)

    def solve_homotopy_source(self, x0, sV):
        """Newton's method with source stepping"""
        x = np.copy(x0)
        totIter = 0
        for lambda_ in np.linspace(start = .1, stop = 1., num = 10):
            def f_Jac_eval(x):
                (iVec, Jac) = self.get_i_Jac(x) 
                return (iVec - lambda_ * sV, Jac)
            
            def f_eval(x):
                iVec = self.get_i(x) 
                return iVec - lambda_ * sV
            (x, res, iterations) = fsolve_Newton(x, f_Jac_eval, f_eval)
            print('lambda = {0}, res = {1}, iter = {2}'.format(lambda_, 
                                                               res, iterations))
            totIter += iterations

        return (x, res, totIter)


#---------------------------------------------------------------------------

class DCNodal(_NLFunction):
    """
    Calculates the DC part of currents and Jacobian

    Matrices and vectors (G, Jac, s) are allocated here. This is to
    centralize all allocations and avoid repetitions.

    Requires a nodal-ready Circuit instance (ckt) instance (see
    make_nodal_circuit())
    """

    def __init__(self, ckt):
        # Init base class
        _NLFunction.__init__(self)

        # Save ckt instance
        self.ckt = ckt
        # Make sure circuit is ready (analysis should take care)
        assert ckt.nD_ref

        # Allocate matrices/vectors
        # G here is G1 = G + G0 in documentation
        self.G = np.zeros((self.ckt.nD_dimension, self.ckt.nD_dimension))
        # Jac is di/dv in doc
        self.Jac = np.zeros((self.ckt.nD_dimension, self.ckt.nD_dimension))
        self.sVec = np.zeros(self.ckt.nD_dimension)
        self.iVec = np.zeros(self.ckt.nD_dimension)
        if hasattr(self.ckt, 'nD_namRClist'):
            # Allocate external currents vector
            self.extSVec = np.zeros(self.ckt.nD_dimension)
        self.refresh()

    def refresh(self):
        """
        Re-generate linear matrices

        Used for parameter sweeps
        """
        self.G *= 0.
        # Generate G matrix (never changes)
        for elem in self.ckt.nD_elemList:
            # All elements have nD_linVCCS (perhaps empty)
            for vccs in elem.nD_linVCCS:
                set_quad(self.G, *vccs)
        # Frequency-defined elements
        for elem in self.ckt.nD_freqDefinedElem:
            set_Jac(self.G, elem.nD_fpos, elem.nD_fneg, 
                    elem.nD_fpos, elem.nD_fneg, elem.get_G_matrix())
            
    def set_ext_currents(self, extIvec):
        """
        Set external currents applied to subcircuit

        extIvec: vector of external currents. Length of this vector
        should be equal to the number of external connections. The sum
        of all currents must be equal to zero (KCL)

        This will fail if not a subcircuit
        """
        # This idea still needs some testing
        assert sum(extIvec[:ncurrents]) == 0
        ncurrents = len(self.ckt.nD_namRClist)
        # Must do the loop in case there are repeated connections
        for val,rcnum in zip(extIvec, self.ckt.nD_namRClist):
            self.extSVec[rcnum] = val

    def get_guess(self):
        """
        Retrieve guesses from vPortGuess in each nonlinear device
        
        Returns a guess vector
        """
        x0 = np.zeros(self.ckt.nD_dimension)
        for elem in self.ckt.nD_nlinElem:
            try:
                # Only add to positive side. This is not the only way
                # and may not work well in some cases but this is a
                # guess anyway
                for i,j in elem.nD_vpos:
                    x0[j] += elem.vPortGuess[i]
            except AttributeError:
                # if vPortGuess not given just leave things unchanged
                pass
        return x0

    def get_source(self):
        """
        Get the source vector considering only the DC source components
        """
        # Erase vector first. 
        try:
            # If subcircuit add external currents
            self.sVec = self.extSVec
        except AttributeError:
            # Not a subcircuit
            self.sVec[:] = 0.
        for elem in self.ckt.nD_sourceDCElem:
            # first get the destination row/columns 
            outTerm = elem.nD_sourceOut
            current = elem.get_DCsource()
            # This may not need optimization because we usually do not
            # have too many independent sources
            # import pdb; pdb.set_trace()
            if outTerm[0] >= 0:
                self.sVec[outTerm[0]] -= current
            if outTerm[1] >= 0:
                self.sVec[outTerm[1]] += current
        return self.sVec
        

    def get_i(self, xVec):
        """
        Calculate total current

        returns iVec = G xVec + i(xVec)

        xVec: input vector of nodal voltages. 

        iVec: output vector of currents
        """
        # Erase arrays
        self.iVec[:] = 0.
        # Linear contribution
        self.iVec += np.dot(self.G, xVec)
        # Nonlinear contribution
        for elem in self.ckt.nD_nlinElem:
            # first have to retrieve port voltages from xVec
            xin = np.zeros(len(elem.controlPorts))
            set_xin(xin, elem.nD_vpos, elem.nD_vneg, xVec)
            outV = elem.eval(xin)
            # Update iVec. outV may have extra charge elements but
            # they are not used in the following
            set_i(self.iVec, elem.nD_cpos, elem.nD_cneg, outV)
        return self.iVec

    def get_i_Jac(self, xVec):
        """
        Calculate total current and Jacobian

        Returns (iVec, Jac)::

            iVec = G xVec + i(xVec)
            Jac = G + (di/dx)(xVec)

        xVec: input vector of nodal voltages. 

        iVec: output vector of currents

        Jac: system Jacobian
        """
        # Erase arrays
        self.iVec[:] = 0.
        self.Jac[:] = 0.
        # Linear contribution
        self.iVec += np.dot(self.G, xVec)
        self.Jac += self.G
        # Nonlinear contribution
        for elem in self.ckt.nD_nlinElem:
            # first have to retrieve port voltages from xVec
            xin = np.zeros(len(elem.controlPorts))
            set_xin(xin, elem.nD_vpos, elem.nD_vneg, xVec)
            (outV, outJac) = elem.eval_and_deriv(xin)
            # Update iVec and Jacobian now. outV may have extra charge
            # elements but they are not used in the following
            set_i(self.iVec, elem.nD_cpos, elem.nD_cneg, outV)
            set_Jac(self.Jac, elem.nD_cpos, elem.nD_cneg, 
                    elem.nD_vpos, elem.nD_vneg, outJac)

        return (self.iVec, self.Jac)

    def save_OP(self, xVec):
        """
        Save nodal voltages in terminals and set OP in elements

        The following information is saved:

          * The nodal voltage vector for the circuit (self.xop)

          * The nodal voltage in each terminal (term.nD_vOP)

          * The port voltages in each nonlinear device (elem.nD_xOP)

          * The operating point (OP) information in nonlinear devices

        """
        # Set nodal voltage of reference to zero
        self.ckt.nD_ref.nD_vOP = 0.
        # Save nodal vector
        self.xop = xVec
        for v,term in zip(xVec, self.ckt.nD_termList):
            term.nD_vOP = v
        for elem in self.ckt.nD_nlinElem:
            # first have to retrieve port voltages from xVec
            xin = np.zeros(len(elem.controlPorts))
            set_xin(xin, elem.nD_vpos, elem.nD_vneg, xVec)
            # Set OP in element (discard return value)
            elem.nD_xOP = xin
            elem.get_OP(xin)

    
#----------------------------------------------------------------------

class TransientNodal(_NLFunction):
    """
    Keeps track of transient analysis equations. 

    Matrices and vectors (G, C, JacI, JacQ, s, etc.) are allocated
    here. This is to centralize all allocations and avoid repetitions.

    Requires a nodal-ready Circuit instance (ckt) instance (see
    make_nodal_circuit())
    """

    def __init__(self, ckt, t0, h, im = None):
        """
        Set up everything to be ready for a time step.

        ckt: circuit instance

        t0: start time

        h: (initial) time step

        im: Integration method instance. Defaults to BE
        """
        # Init base class
        _NLFunction.__init__(self)

        # Save ckt instance
        self.ckt = ckt
        # Make sure circuit is ready (analysis should take care)
        assert ckt.nD_ref
        # Time variables: current time and time step
        self.ctime = t0
        self.h = h

        if im == None:
            self.im = BEuler()
        else:
            self.im = im
        self.im.set_h(self.h)

        # Allocate matrices/vectors
        # G' and C
        self.Gp = np.zeros((self.ckt.nD_dimension, self.ckt.nD_dimension))
        self.C = np.zeros((self.ckt.nD_dimension, self.ckt.nD_dimension))
        # iVec = G' x + i'(x)   total current
        self.iVec = np.zeros(self.ckt.nD_dimension)
        # System Jacobian: G' + di'/dx
        self.Jac = np.zeros((self.ckt.nD_dimension, self.ckt.nD_dimension))
        # Total charge: C x + q(x)
        self.qVec = np.zeros(self.ckt.nD_dimension)
        # Source vector at current time s(ctime) or sometimes s'(ctime)
        self.sVec = np.zeros(self.ckt.nD_dimension)

#        if hasattr(self.ckt, 'nD_namRClist'):
#            # Allocate external currents vector
#            self.extSVec = np.zeros(self.ckt.nD_dimension)
        self.refresh()

    def refresh(self):
        """
        Re-generate linear matrices

        Used for parametric sweeps
        """
        self.Gp *= 0.
        # Generate G matrix (never changes)
        for elem in self.ckt.nD_elemList:
            # All elements have nD_linVCCS (perhaps empty)
            for vccs in elem.nD_linVCCS:
                set_quad(self.Gp, *vccs)
            for vccs in elem.nD_linVCQS:
                set_quad(self.C, *vccs)

        # In the future we may have to modify set_quad to accept a
        # scaling factor
        self.Gp += self.im.a0 * self.C

#        # Frequency-defined elements not included for now
#        for elem in self.ckt.nD_freqDefinedElem:
#            set_Jac(self.G, elem.nD_fpos, elem.nD_fneg, 
#                    elem.nD_fpos, elem.nD_fneg, elem.get_G_matrix())

    def set_IC(self):
        """
        Set initial conditions (ICs)

        Retrieves ICs from DC operating point info in elements
        """
        # Nodal variables
        xVec = np.zeros(self.ckt.nD_dimension)
        # Get nodal voltages for tstart
        for i,term in enumerate(self.ckt.nD_termList):
            xVec[i] = term.nD_vOP
        # Get nonlinear charges
        self.qVec *= 0.
        for elem in self.ckt.nD_nlinElem:
            # Get OP from element
            outV = elem.eval(elem.nD_xOP)
            set_i(self.qVec, elem.nD_qpos, elem.nD_qneg, 
                  outV[len(elem.csOutPorts):])
        # Add linear charges
        self.qVec += np.dot(self.C, xVec)
        # Do this to store qvec in im (if needed)
        self.im.f_n1(self.qVec)


    def advance(self, xVec):
        """ 
        Advance one time step 

        Set up equations for next time step to be solved by
        fsolve. Returns the right-hand side source vector plus the
        effect of charges (s' in derivation).

        xVec: solution from current time step
        """
        # Advance time
        self.ctime += self.h
        self.get_source()

        # Calculate total q vector
        self.qVec *= 0.
        for elem in self.ckt.nD_nlinElem:
            # first have to retrieve port voltages from xVec
            xin = np.zeros(len(elem.controlPorts))
            set_xin(xin, elem.nD_vpos, elem.nD_vneg, xVec)  
            outV = elem.eval(xin)
            set_i(self.qVec, elem.nD_qpos, elem.nD_qneg, 
                  outV[len(elem.csOutPorts):])
        # Add linear charges
        self.qVec += np.dot(self.C, xVec)

        self.sVec += self.im.f_n1(self.qVec)

        return self.sVec

            
    def get_source(self, ctime = None):
        """
        Get the source vector considering DC and TD source components

        ctime: current time. Defaults to internal ctime variable
        """
        if ctime == None:
            ctime = self.ctime
        # Erase vector first. 
        try:
            # If subcircuit add external currents
            self.sVec = self.extSVec
        except AttributeError:
            # Not a subcircuit
            self.sVec[:] = 0.
        for elem in self.ckt.nD_sourceDCElem:
            # first get the destination row/columns 
            outTerm = elem.nD_sourceOut
            current = elem.get_DCsource()
            # This may not need optimization because we usually do not
            # have too many independent sources
            # import pdb; pdb.set_trace()
            if outTerm[0] >= 0:
                self.sVec[outTerm[0]] -= current
            if outTerm[1] >= 0:
                self.sVec[outTerm[1]] += current
        for elem in self.ckt.nD_sourceTDElem:
            # first get the destination row/columns 
            outTerm = elem.nD_sourceOut
            current = elem.get_TDsource(ctime)
            # This may not need optimization because we usually do not
            # have too many independent sources
            # import pdb; pdb.set_trace()
            if outTerm[0] >= 0:
                self.sVec[outTerm[0]] -= current
            if outTerm[1] >= 0:
                self.sVec[outTerm[1]] += current
        return self.sVec
        

    def get_i(self, xVec):
        """
        Calculate total current

        returns iVec = G' xVec + i'(xVec)

        xVec: input vector of nodal voltages. 

        iVec: output vector of currents
        """
        # Erase arrays
        self.iVec[:] = 0.
        # Linear contribution
        self.iVec += np.dot(self.Gp, xVec)
        # Nonlinear contribution
        for elem in self.ckt.nD_nlinElem:
            # first have to retrieve port voltages from xVec
            xin = np.zeros(len(elem.controlPorts))
            set_xin(xin, elem.nD_vpos, elem.nD_vneg, xVec)
            outV = elem.eval(xin)
            # Update iVec. outV may have extra charge elements but
            # they are not used in the following
            set_i(self.iVec, elem.nD_cpos, elem.nD_cneg, outV)
            set_i(self.iVec, elem.nD_qpos, elem.nD_qneg, 
                  self.im.a0 * outV[len(elem.csOutPorts):])
        return self.iVec

    def get_i_Jac(self, xVec):
        """
        Calculate total current and Jacobian

        Returns (iVec, Jac)::

            iVec = G' xVec + i'(xVec)
            Jac = G' + (di'/dx)(xVec)

        xVec: input vector of nodal voltages. 

        iVec: output vector of currents

        Jac: system Jacobian
        """
        # Erase arrays
        self.iVec[:] = 0.
        self.Jac[:] = 0.
        # Linear contribution
        self.iVec += np.dot(self.Gp, xVec)
        self.Jac += self.Gp
        # Nonlinear contribution
        for elem in self.ckt.nD_nlinElem:
            # first have to retrieve port voltages from xVec
            xin = np.zeros(len(elem.controlPorts))
            set_xin(xin, elem.nD_vpos, elem.nD_vneg, xVec)
            (outV, outJac) = elem.eval_and_deriv(xin)
            # Update iVec and Jacobian now. outV may have extra charge
            # elements but they are not used in the following
            set_i(self.iVec, elem.nD_cpos, elem.nD_cneg, outV)
            set_i(self.iVec, elem.nD_qpos, elem.nD_qneg, 
                  self.im.a0 * outV[len(elem.csOutPorts):])
            set_Jac(self.Jac, elem.nD_cpos, elem.nD_cneg, 
                    elem.nD_vpos, elem.nD_vneg, outJac)
            qJac = self.im.a0 * outJac[len(elem.csOutPorts):,:]
            set_Jac(self.Jac, elem.nD_qpos, elem.nD_qneg, 
                    elem.nD_vpos, elem.nD_vneg, qJac)

        return (self.iVec, self.Jac)


