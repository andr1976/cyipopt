#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cyipopt: Python wrapper for the Ipopt optimization package, written in Cython.

Copyright (C) 2012-2015 Amit Aides
Copyright (C) 2015-2017 Matthias Kümmerer
Copyright (C) 2017-2021 cyipopt developers

License: EPL 1.0
"""

from __future__ import absolute_import, unicode_literals
import sys

from builtins import bytes  # from the future package
import numpy as np
try:
    import scipy
except ImportError:  # scipy is not installed
    SCIPY_INSTALLED = False
else:
    SCIPY_INSTALLED = True
    del scipy
    from scipy.optimize import approx_fprime
    try:
        from scipy.optimize import OptimizeResult
    except ImportError:
        # in scipy 0.14 Result was renamed to OptimzeResult
        from scipy.optimize import Result
        OptimizeResult = Result

import cyipopt


class IpoptProblemWrapper(object):
    """Class used to map an scipy minimize definition to a cyipopt problem.

    Parameters
    ==========
    fun : callable
        The objective function to be minimized: ``fun(x, *args, **kwargs) ->
        float``.
    args : tuple, optional
        Extra arguments passed to the objective function and its derivatives
        (``fun``, ``jac``, ``hess``).
    kwargs : dictionary, optional
        Extra keyword arguments passed to the objective function and its
        derivatives (``fun``, ``jac``, ``hess``).
    jac : callable, optional
        The Jacobian of the objective function: ``jac(x, *args, **kwargs) ->
        ndarray, shape(n, )``. If ``None``, SciPy's ``approx_fprime`` is used.
    hess : callable, optional
        If ``None``, the Hessian is computed using IPOPT's numerical methods.
        Explicitly defined Hessians are not yet supported for this class.
    hessp : callable, optional
        If ``None``, the Hessian is computed using IPOPT's numerical methods.
        Explicitly defined Hessians are not yet supported for this class.
    constraints : {Constraint, dict} or List of {Constraint, dict}, optional
        See https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html
        for more information.
    eps : float, optional
        Epsilon used in finite differences.

    """
    def __init__(self, fun, args=(), kwargs=None, jac=None, hess=None,
                 hessp=None, constraints=(), eps=1e-8):
        if not SCIPY_INSTALLED:
            msg = 'Install SciPy to use the `IpoptProblemWrapper` class.'
            raise ImportError()
        self.fun_with_jac = None
        self.last_x = None
        if hess is not None or hessp is not None:
            msg = 'Using hessian matrixes is not yet implemented!'
            raise NotImplementedError(msg)
        if jac is None:
            jac = lambda x0, *args, **kwargs: approx_fprime(x0, fun, eps,
                                                            *args, **kwargs)
        elif jac is True:
            self.fun_with_jac = fun
        elif not callable(jac):
            raise NotImplementedError('jac has to be bool or a function')
        self.fun = fun
        self.jac = jac
        self.args = args
        self.kwargs = kwargs or {}
        self._constraint_funs = []
        self._constraint_jacs = []
        self._constraint_args = []
        self._constraint_kwargs = []
        if isinstance(constraints, dict):
            constraints = (constraints, )
        for con in constraints:
            con_fun = con['fun']
            con_jac = con.get('jac', None)
            con_args = con.get('args', [])
            con_kwargs = con.get('kwargs', [])
            if con_jac is None:
                con_jac = lambda x0, *args, **kwargs: approx_fprime(x0, con_fun, eps, *args, **kwargs)
            self._constraint_funs.append(con_fun)
            self._constraint_jacs.append(con_jac)
            self._constraint_args.append(con_args)
            self._constraint_kwargs.append(con_kwargs)
        # Set up evaluation counts
        self.nfev = 0
        self.njev = 0
        self.nit = 0

    def evaluate_fun_with_grad(self, x):
        if self.last_x is None or not np.all(self.last_x == x):
            self.last_x = x
            self.nfev += 1
            self.last_value = self.fun(x, *self.args, **self.kwargs)
        return self.last_value

    def objective(self, x):
        if self.fun_with_jac:
            return self.evaluate_fun_with_grad(x)[0]

        self.nfev += 1
        return self.fun(x, *self.args, **self.kwargs)

    def gradient(self, x, **kwargs):
        if self.fun_with_jac:
            return self.evaluate_fun_with_grad(x)[1]

        self.njev += 1
        return self.jac(x, *self.args, **self.kwargs)  # .T

    def constraints(self, x):
        con_values = []
        for fun, args in zip(self._constraint_funs, self._constraint_args):
            con_values.append(fun(x, *args))
        return np.hstack(con_values)

    def jacobian(self, x):
        con_values = []
        for fun, args in zip(self._constraint_jacs, self._constraint_args):
            con_values.append(fun(x, *args))
        return np.vstack(con_values)

    def intermediate(
            self,
            alg_mod,
            iter_count,
            obj_value,
            inf_pr,
            inf_du,
            mu,
            d_norm,
            regularization_size,
            alpha_du,
            alpha_pr,
            ls_trials
            ):

        self.nit = iter_count


def get_bounds(bounds):
    if bounds is None:
        return None, None
    else:
        lb = [b[0] for b in bounds]
        ub = [b[1] for b in bounds]
        return lb, ub


def get_constraint_bounds(constraints, x0, INF=1e19):
    if isinstance(constraints, dict):
        constraints = (constraints, )
    cl = []
    cu = []
    if isinstance(constraints, dict):
        constraints = (constraints, )
    for con in constraints:
        m = len(np.atleast_1d(con['fun'](x0, *con.get('args', []))))
        cl.extend(np.zeros(m))
        if con['type'] == 'eq':
            cu.extend(np.zeros(m))
        elif con['type'] == 'ineq':
            cu.extend(INF*np.ones(m))
        else:
            raise ValueError(con['type'])
    cl = np.array(cl)
    cu = np.array(cu)

    return cl, cu


def replace_option(options, oldname, newname):
    if oldname in options:
        if newname not in options:
            options[newname] = options.pop(oldname)


def convert_to_bytes(options):
    if sys.version_info >= (3, 0):
        for key in list(options.keys()):
            try:
                if bytes(key, 'utf-8') != key:
                    options[bytes(key, 'utf-8')] = options[key]
                    options.pop(key)
            except TypeError:
                pass


def minimize_ipopt(fun, x0, args=(), kwargs=None, method=None, jac=None,
                   hess=None, hessp=None, bounds=None, constraints=(),
                   tol=None, callback=None, options=None):
    """
    Minimize a function using ipopt. The call signature is exactly like for
    ``scipy.optimize.mimize``. In options, all options are directly passed to
    ipopt. Check [http://www.coin-or.org/Ipopt/documentation/node39.html] for
    details. The options ``disp`` and ``maxiter`` are automatically mapped to
    their ipopt-equivalents ``print_level`` and ``max_iter``.
    """
    if not SCIPY_INSTALLED:
        msg = 'Install SciPy to use the `minimize_ipopt` function.'
        raise ImportError(msg)

    _x0 = np.atleast_1d(x0)
    problem = IpoptProblemWrapper(fun, args=args, kwargs=kwargs, jac=jac,
                                  hess=hess, hessp=hessp,
                                  constraints=constraints)
    lb, ub = get_bounds(bounds)

    cl, cu = get_constraint_bounds(constraints, x0)

    if options is None:
        options = {}

    nlp = cyipopt.Problem(n=len(_x0),
                          m=len(cl),
                          problem_obj=problem,
                          lb=lb,
                          ub=ub,
                          cl=cl,
                          cu=cu)

    # python3 compatibility
    convert_to_bytes(options)

    # Rename some default scipy options
    replace_option(options, b'disp', b'print_level')
    replace_option(options, b'maxiter', b'max_iter')
    if b'print_level' not in options:
        options[b'print_level'] = 0
    if b'tol' not in options:
        options[b'tol'] = tol or 1e-8
    if b'mu_strategy' not in options:
        options[b'mu_strategy'] = b'adaptive'
    if b'hessian_approximation' not in options:
        if hess is None and hessp is None:
            options[b'hessian_approximation'] = b'limited-memory'
    for option, value in options.items():
        try:
            nlp.add_option(option, value)
        except TypeError as e:
            msg = 'Invalid option for IPOPT: {0}: {1} (Original message: "{2}")'
            raise TypeError(msg.format(option, value, e))

    x, info = nlp.solve(_x0)

    if np.asarray(x0).shape == ():
        x = x[0]

    return OptimizeResult(x=x,
                          success=info['status'] == 0,
                          status=info['status'],
                          message=info['status_msg'],
                          fun=info['obj_val'],
                          info=info,
                          nfev=problem.nfev,
                          njev=problem.njev,
                          nit=problem.nit)
