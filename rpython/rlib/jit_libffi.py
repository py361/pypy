
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rtyper.extregistry import ExtRegistryEntry
from rpython.rlib import clibffi, jit
from rpython.rlib.nonconst import NonConstant


FFI_CIF = clibffi.FFI_CIFP.TO
FFI_TYPE = clibffi.FFI_TYPE_P.TO
FFI_TYPE_P = clibffi.FFI_TYPE_P
FFI_TYPE_PP = clibffi.FFI_TYPE_PP
FFI_ABI = clibffi.FFI_ABI
FFI_TYPE_STRUCT = clibffi.FFI_TYPE_STRUCT
SIZE_OF_FFI_ARG = rffi.sizeof(clibffi.ffi_arg)

# Usage: for each C function, make one CIF_DESCRIPTION block of raw
# memory.  Initialize it by filling all its fields apart from 'cif'.
# The 'atypes' points to an array of ffi_type pointers; a reasonable
# place to locate this array's memory is in the same block of raw
# memory, by allocating more than sizeof(CIF_DESCRIPTION).
#
# The four fields 'abi', 'nargs', 'rtype', 'atypes' are the same as
# the arguments to ffi_prep_cif().
#
# Following this, we find jit_libffi-specific information:
#
#  - 'exchange_size': an integer that tells how big a buffer we must
#    allocate to do the call; this buffer should have enough room at the
#    beginning for an array of NARGS pointers which is initialized
#    internally by jit_ffi_call().
#
#  - 'exchange_result': the offset in that buffer for the result of the call.
#    (this and the other offsets must be at least NARGS * sizeof(void*).)
#
#  - 'exchange_result_libffi': the actual offset passed to ffi_call().
#    Differs on big-endian machines if the result is an integer type smaller
#    than SIZE_OF_FFI_ARG (blame libffi).
#
#  - 'exchange_args[nargs]': the offset in that buffer for each argument.

CIF_DESCRIPTION = lltype.Struct(
    'CIF_DESCRIPTION',
    ('cif', FFI_CIF),
    ('abi', lltype.Signed),    # these 4 fields could also be read directly
    ('nargs', lltype.Signed),  # from 'cif', but doing so adds a dependency
    ('rtype', FFI_TYPE_P),     # on the exact fields available from ffi_cif.
    ('atypes', FFI_TYPE_PP),   #
    ('exchange_size', lltype.Signed),
    ('exchange_result', lltype.Signed),
    ('exchange_result_libffi', lltype.Signed),
    ('exchange_args', lltype.Array(lltype.Signed,
                          hints={'nolength': True, 'immutable': True})),
    hints={'immutable': True})

CIF_DESCRIPTION_P = lltype.Ptr(CIF_DESCRIPTION)


def jit_ffi_prep_cif(cif_description):
    """Minimal wrapper around ffi_prep_cif().  Call this after
    cif_description is initialized, in order to fill the last field: 'cif'.
    """
    res = clibffi.c_ffi_prep_cif(cif_description.cif,
                                 cif_description.abi,
                                 cif_description.nargs,
                                 cif_description.rtype,
                                 cif_description.atypes)
    return rffi.cast(lltype.Signed, res)


# =============================
# jit_ffi_call and its helpers
# =============================

## Problem: jit_ffi_call is turned into call_release_gil by pyjitpl. Before
## the refactor-call_release_gil branch, the resulting code looked like this:
##
##     buffer = ...
##     i0 = call_release_gil(...)
##     guard_not_forced()
##     setarray_item_raw(buffer, ..., i0)
##
## The problem is that the result box i0 was generated freshly inside pyjitpl,
## and the codewriter did not know about its liveness: the result was that i0
## was not in the fail_args of guard_not_forced. See
## test_fficall::test_guard_not_forced_fails for a more detalied explanation
## of the problem.
##
## The solution is to create a new separate operation libffi_save_result whose
## job is to write the result in the exchange_buffer: during normal execution
## this is a no-op because the buffer is already filled by libffi, but during
## jitting the behavior is to actually write into the buffer.
##
## The result is that now the jitcode looks like this:
##
##     %i0 = libffi_call_int(...)
##     -live-
##     libffi_save_result_int(..., %i0)
##
## the "-live-" is the key, because it make sure that the value is not lost if
## guard_not_forced fails.


def jit_ffi_call(cif_description, func_addr, exchange_buffer):
    """Wrapper around ffi_call().  Must receive a CIF_DESCRIPTION_P that
    describes the layout of the 'exchange_buffer'.
    """
    reskind = types.getkind(cif_description.rtype)
    if reskind == 'v':
        jit_ffi_call_impl_void(cif_description, func_addr, exchange_buffer)
    elif reskind == 'f' or reskind == 'L': # L is for longlongs, on 32bit
        result = jit_ffi_call_impl_float(cif_description, func_addr, exchange_buffer)
        jit_ffi_save_result('float', cif_description, exchange_buffer, result)
    elif reskind == 'i' or reskind == 'u':
        result = jit_ffi_call_impl_int(cif_description, func_addr, exchange_buffer)
        jit_ffi_save_result('int', cif_description, exchange_buffer, result)
    else:
        assert False, 'Unsupported result kind'


# we must return a NonConstant else we get the constant -1 as the result of
# the flowgraph, and the codewriter does not produce a box for the
# result. Note that when not-jitted, the result is unused, but when jitted the
# box of the result contains the actual value returned by the C function.

@jit.oopspec("libffi_call(cif_description,func_addr,exchange_buffer)")
def jit_ffi_call_impl_int(cif_description, func_addr, exchange_buffer):
    jit_ffi_call_impl_any(cif_description, func_addr, exchange_buffer)
    return NonConstant(-1)

@jit.oopspec("libffi_call(cif_description,func_addr,exchange_buffer)")
def jit_ffi_call_impl_float(cif_description, func_addr, exchange_buffer):
    jit_ffi_call_impl_any(cif_description, func_addr, exchange_buffer)
    return NonConstant(-1.0)

@jit.oopspec("libffi_call(cif_description,func_addr,exchange_buffer)")
def jit_ffi_call_impl_void(cif_description, func_addr, exchange_buffer):
    jit_ffi_call_impl_any(cif_description, func_addr, exchange_buffer)
    return None

def jit_ffi_call_impl_any(cif_description, func_addr, exchange_buffer):
    """
    This is the function which actually calls libffi. All the rest if just
    infrastructure to convince the JIT to pass a typed result box to
    jit_ffi_save_result
    """
    buffer_array = rffi.cast(rffi.VOIDPP, exchange_buffer)
    for i in range(cif_description.nargs):
        data = rffi.ptradd(exchange_buffer, cif_description.exchange_args[i])
        buffer_array[i] = data
    resultdata = rffi.ptradd(exchange_buffer,
                             cif_description.exchange_result_libffi)
    clibffi.c_ffi_call(cif_description.cif, func_addr,
                       rffi.cast(rffi.VOIDP, resultdata),
                       buffer_array)
    return -1



def jit_ffi_save_result(kind, cif_description, exchange_buffer, result):
    """
    This is a no-op during normal execution, but actually fills the buffer
    when jitted
    """
    pass

class Entry(ExtRegistryEntry):
    _about_ = jit_ffi_save_result

    def compute_result_annotation(self, kind_s, *args_s):
        from rpython.annotator import model as annmodel
        assert isinstance(kind_s, annmodel.SomeString)
        assert kind_s.const in ('int', 'float')

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        vlist = hop.inputargs(lltype.Void, *hop.args_r[1:])
        return hop.genop('jit_ffi_save_result', vlist,
                         resulttype=lltype.Void)
    

# ____________________________________________________________

class types(object):
    """
    This namespace contains the mapping the JIT needs from ffi types to
    a less strict "kind" character.
    """

    @classmethod
    def _import(cls):
        prefix = 'ffi_type_'
        for key, value in clibffi.__dict__.iteritems():
            if key.startswith(prefix):
                name = key[len(prefix):]
                setattr(cls, name, value)
        cls.slong = clibffi.cast_type_to_ffitype(rffi.LONG)
        cls.ulong = clibffi.cast_type_to_ffitype(rffi.ULONG)
        cls.slonglong = clibffi.cast_type_to_ffitype(rffi.LONGLONG)
        cls.ulonglong = clibffi.cast_type_to_ffitype(rffi.ULONGLONG)
        cls.signed = clibffi.cast_type_to_ffitype(rffi.SIGNED)
        cls.wchar_t = clibffi.cast_type_to_ffitype(lltype.UniChar)
        del cls._import

    @staticmethod
    @jit.elidable
    def getkind(ffi_type):
        """Returns 'v' for void, 'f' for float, 'i' for signed integer,
        'u' for unsigned integer, 'S' for singlefloat, 'L' for long long
        integer (signed or unsigned), '*' for struct, or '?' for others
        (e.g. long double).
        """
        if   ffi_type == types.void:    return 'v'
        elif ffi_type == types.double:  return 'f'
        elif ffi_type == types.float:   return 'S'
        elif ffi_type == types.pointer: return 'i'
        #
        elif ffi_type == types.schar:   return 'i'
        elif ffi_type == types.uchar:   return 'u'
        elif ffi_type == types.sshort:  return 'i'
        elif ffi_type == types.ushort:  return 'u'
        elif ffi_type == types.sint:    return 'i'
        elif ffi_type == types.uint:    return 'u'
        elif ffi_type == types.slong:   return 'i'
        elif ffi_type == types.ulong:   return 'u'
        #
        elif ffi_type == types.sint8:   return 'i'
        elif ffi_type == types.uint8:   return 'u'
        elif ffi_type == types.sint16:  return 'i'
        elif ffi_type == types.uint16:  return 'u'
        elif ffi_type == types.sint32:  return 'i'
        elif ffi_type == types.uint32:  return 'u'
        ## (note that on 64-bit platforms, types.sint64 == types.slong and the
        ## case == caught above)
        elif ffi_type == types.sint64:  return 'L'
        elif ffi_type == types.uint64:  return 'L'
        #
        elif types.is_struct(ffi_type): return '*'
        return '?'

    @staticmethod
    @jit.elidable
    def is_struct(ffi_type):
        return rffi.getintfield(ffi_type, 'c_type') == FFI_TYPE_STRUCT

types._import()
