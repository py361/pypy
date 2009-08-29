import sys
import ctypes
import py
from pypy.rpython.lltypesystem import lltype, llmemory, rffi
from pypy.rpython.llinterp import LLInterpreter
from pypy.rlib.objectmodel import we_are_translated
from pypy.jit.metainterp import history
from pypy.jit.backend.x86.assembler import Assembler386, MAX_FAIL_BOXES
from pypy.jit.backend.x86.assembler import x86Logger
from pypy.jit.backend.llsupport.llmodel import AbstractLLCPU

history.TreeLoop._x86_compiled = 0
history.TreeLoop._x86_bootstrap_code = 0


class CPU386(AbstractLLCPU):
    debug = True
    logger_cls = x86Logger

    BOOTSTRAP_TP = lltype.FuncType([], lltype.Signed)

    def __init__(self, rtyper, stats, translate_support_code=False,
                 mixlevelann=None, gcdescr=None):
        AbstractLLCPU.__init__(self, rtyper, stats, translate_support_code,
                               gcdescr)
        if translate_support_code:
            assert mixlevelann
            self.mixlevelann = mixlevelann
        else:
            self.current_interpreter = LLInterpreter(self.rtyper)

            def _store_exception(lle):
                tp_i = self.cast_ptr_to_int(lle.args[0])
                v_i = self.cast_gcref_to_int(lle.args[1])
                self.assembler._exception_data[0] = tp_i
                self.assembler._exception_data[1] = v_i
            
            self.current_interpreter._store_exception = _store_exception
        TP = lltype.GcArray(llmemory.GCREF)
        self._bootstrap_cache = {}
        self._guard_list = []
        self.setup()
        self.caught_exception = None
        if rtyper is not None: # for tests
            self.lltype2vtable = rtyper.lltype_to_vtable_mapping()

    def setup(self):
        self.assembler = Assembler386(self, self.translate_support_code)

    def setup_once(self):
        pass

    def get_exception(self):
        self.assembler.make_sure_mc_exists()
        return self.assembler._exception_bck[0]

    def get_exc_value(self):
        self.assembler.make_sure_mc_exists()
        return self.cast_int_to_gcref(self.assembler._exception_bck[1])

    def clear_exception(self):
        self.assembler.make_sure_mc_exists()
        self.assembler._exception_bck[0] = 0
        self.assembler._exception_bck[1] = 0

    def compile_operations(self, tree, bridge=None):
        old_loop = tree._x86_compiled
        if old_loop:
            olddepth = tree._x86_stack_depth
            oldlocs = tree.arglocs
        else:
            oldlocs = None
            olddepth = 0
        stack_depth = self.assembler.assemble(tree)
        newlocs = tree.arglocs
        if old_loop != 0:
            self.assembler.patch_jump(old_loop, tree._x86_compiled,
                                      oldlocs, newlocs, olddepth,
                                      tree._x86_stack_depth)

    def get_bootstrap_code(self, loop):
        addr = loop._x86_bootstrap_code
        if not addr:
            arglocs = loop.arglocs
            addr = self.assembler.assemble_bootstrap_code(loop._x86_compiled,
                                                          arglocs,
                                                          loop.inputargs,
                                                          loop._x86_stack_depth)
            loop._x86_bootstrap_code = addr
        func = rffi.cast(lltype.Ptr(self.BOOTSTRAP_TP), addr)
        return func

    def execute_operations(self, loop, verbose=False):
        assert isinstance(verbose, bool)
        func = self.get_bootstrap_code(loop)
        # debug info
        #if self.debug and not we_are_translated():
        #    values_repr = ", ".join([str(values_as_int[i]) for i in
        #                             range(len(valueboxes))])
        #    llop.debug_print(lltype.Void, 'exec:', name, values_repr)
        #self.assembler.logger.log_call(valueboxes) --- XXX
        guard_index = self.execute_call(loop, func, verbose)
        self._guard_index = guard_index # for tests
        op = self._guard_list[guard_index]
        if verbose:
            print "Leaving at: %d" % self.assembler.fail_boxes_int[
                len(op.args)]
        return op

    def set_future_value_int(self, index, intvalue):
        assert index < MAX_FAIL_BOXES, "overflow!"
        self.assembler.fail_boxes_int[index] = intvalue

    def set_future_value_ptr(self, index, ptrvalue):
        assert index < MAX_FAIL_BOXES, "overflow!"
        self.assembler.fail_boxes_ptr[index] = ptrvalue

    def get_latest_value_int(self, index):
        return self.assembler.fail_boxes_int[index]

    def get_latest_value_ptr(self, index):
        ptrvalue = self.assembler.fail_boxes_ptr[index]
        # clear after reading
        self.assembler.fail_boxes_ptr[index] = lltype.nullptr(
            llmemory.GCREF.TO)
        return ptrvalue

    def execute_call(self, loop, func, verbose):
        # help flow objspace
        prev_interpreter = None
        if not self.translate_support_code:
            prev_interpreter = LLInterpreter.current_interpreter
            LLInterpreter.current_interpreter = self.current_interpreter
        res = 0
        try:
            self.caught_exception = None
            if verbose:
                print "Entering: %d" % rffi.cast(lltype.Signed, func)
            #llop.debug_print(lltype.Void, ">>>> Entering",
            #                 rffi.cast(lltype.Signed, func))
            res = func()
            #llop.debug_print(lltype.Void, "<<<< Back")
            self.reraise_caught_exception()
        finally:
            if not self.translate_support_code:
                LLInterpreter.current_interpreter = prev_interpreter
        return res

    def reraise_caught_exception(self):
        # this helper is in its own function so that the call to it
        # shows up in traceback -- useful to avoid confusing tracebacks,
        # which are typical when using the 3-arguments raise.
        if self.caught_exception is not None:
            if not we_are_translated():
                exc, val, tb = self.caught_exception
                raise exc, val, tb
            else:
                exc = self.caught_exception
                raise exc

    def make_guard_index(self, guard_op):
        index = len(self._guard_list)
        self._guard_list.append(guard_op)
        return index


CPU = CPU386

import pypy.jit.metainterp.executor
pypy.jit.metainterp.executor.make_execute_list(CPU)
