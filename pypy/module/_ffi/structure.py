
""" Interpreter-level implementation of structure, exposing ll-structure
to app-level with apropriate interface
"""

from pypy.interpreter.baseobjspace import W_Root, Wrappable
from pypy.interpreter.gateway import interp2app, ObjSpace
from pypy.interpreter.argument import Arguments
from pypy.interpreter.typedef import TypeDef, GetSetProperty
from pypy.rpython.lltypesystem import lltype, rffi
from pypy.interpreter.error import OperationError, wrap_oserror
# XXX we've got the very same info in two places - one is native_fmttable
# the other one is in rlib/libffi, we should refactor it to reuse the same
# logic, I'll not touch it by now, and refactor it later
from pypy.module.struct.nativefmttable import native_fmttable as struct_native_fmttable
from pypy.module._ffi.interp_ffi import wrap_value, unwrap_value, _get_type, TYPEMAP

native_fmttable = {}
for key, value in struct_native_fmttable.items():
    native_fmttable[key] = {'size': value['size'],
                            'alignment': value.get('alignment', value['size'])}

def unpack_fields(space, w_fields):
    fields_w = space.unpackiterable(w_fields)
    fields = []
    for w_tup in fields_w:
        l_w = space.unpackiterable(w_tup)
        if not len(l_w) == 2:
            raise OperationError(space.w_ValueError, space.wrap(
                "Expected list of 2-size tuples"))
        name, code = space.str_w(l_w[0]), space.str_w(l_w[1])
        _get_type(space, code) # be paranoid about types
        fields.append((name, code))
    return fields

def size_and_pos(fields):
    size = native_fmttable[fields[0][1]]['size']
    pos = [0]
    for i in range(1, len(fields)):
        field_desc = native_fmttable[fields[i][1]]
        missing = size % field_desc.get('alignment', 1)
        if missing:
            size += field_desc['alignment'] - missing
        pos.append(size)
        size += field_desc['size']
    return size, pos


class W_Structure(Wrappable):
    def __init__(self, space, w_fields):
        fields = unpack_fields(space, w_fields)
        name_to_offset = {}
        for i in range(len(fields)):
            name, letter = fields[i]
            if letter not in TYPEMAP:
                raise OperationError(space.w_ValueError, space.wrap(
                    "Unkown type letter %s" % (letter,)))
            if name in name_to_offset:
                raise OperationError(space.w_ValueError, space.wrap(
                    "duplicate field name %s" % (name, )))
            name_to_offset[name] = i
        size, pos = size_and_pos(fields)
        self.size = size
        self.ll_positions = pos
        self.fields = fields
        self.name_to_offset = name_to_offset

    def descr_call(self, space, __args__):
        args_w, kwargs_w = __args__.unpack()
        if args_w:
            if len(args_w) > 1:
                raise OperationError(
                        space.w_TypeError,
                        space.wrap("Can give at most one non-keyword argument"))
            if kwargs_w:
                raise OperationError(
                        space.w_TypeError,
                        space.wrap("Keyword arguments not allowed when passing address argument"))
            return space.wrap(W_StructureInstance(space, self, args_w[0], None))
        return space.wrap(W_StructureInstance(space, self, None, kwargs_w))

def descr_new_structure(space, w_type, w_fields):
    return W_Structure(space, w_fields)

W_Structure.typedef = TypeDef(
    'Structure',
    __new__     = interp2app(descr_new_structure),
    __call__ = interp2app(W_Structure.descr_call,
                          unwrap_spec=['self', ObjSpace, Arguments]),
)
W_Structure.typedef.acceptable_as_base_class = False

def push_field(self, num, value):
    ptr = rffi.ptradd(self.ll_buffer, self.shape.ll_positions[num])
    TP = lltype.typeOf(value)
    T = lltype.Ptr(rffi.CArray(TP))
    rffi.cast(T, ptr)[0] = value
push_field._annspecialcase_ = 'specialize:argtype(2)'
    
def cast_pos(self, i, ll_t):
    pos = rffi.ptradd(self.ll_buffer, self.shape.ll_positions[i])
    TP = lltype.Ptr(rffi.CArray(ll_t))
    return rffi.cast(TP, pos)[0]
cast_pos._annspecialcase_ = 'specialize:arg(2)'

class W_StructureInstance(Wrappable):
    def __init__(self, space, shape, w_address, fieldinits_w):
        self.free_afterwards = False
        self.shape = shape
        if w_address is not None:
            self.ll_buffer = rffi.cast(rffi.VOIDP, space.int_w(w_address))
        else:
            self.free_afterwards = True
            self.ll_buffer = lltype.malloc(rffi.VOIDP.TO, shape.size, flavor='raw',
                                           zero=True)
        if fieldinits_w:
            for field, w_value in fieldinits_w.iteritems():
                self.setattr(space, field, w_value)

    def getindex(self, space, attr):
        try:
            return self.shape.name_to_offset[attr]
        except KeyError:
            raise OperationError(space.w_AttributeError, space.wrap(
                "C Structure has no attribute %s" % attr))

    def getattr(self, space, attr):
        i = self.getindex(space, attr)
        _, c = self.shape.fields[i]
        return wrap_value(space, cast_pos, self, i, c)
    getattr.unwrap_spec = ['self', ObjSpace, str]

    def setattr(self, space, attr, w_value):
        i = self.getindex(space, attr)
        _, c = self.shape.fields[i]
        unwrap_value(space, push_field, self, i, c, w_value, None)
    setattr.unwrap_spec = ['self', ObjSpace, str, W_Root]

    def __del__(self):
        if self.free_afterwards:
            lltype.free(self.ll_buffer, flavor='raw')

    def getbuffer(space, self):
        return space.wrap(rffi.cast(rffi.INT, self.ll_buffer))

W_StructureInstance.typedef = TypeDef(
    'StructureInstance',
    __getattr__ = interp2app(W_StructureInstance.getattr),
    __setattr__ = interp2app(W_StructureInstance.setattr),
    buffer      = GetSetProperty(W_StructureInstance.getbuffer),
)
W_StructureInstance.typedef.acceptable_as_base_class = False

