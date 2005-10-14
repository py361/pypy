
"""typesystem.py -- Typesystem-specific operations for RTyper."""

from pypy.annotation.pairtype import extendabletype

from pypy.rpython.ootypesystem import ootype
from pypy.rpython.lltypesystem import lltype

class TypeSystem(object):
    __metaclass__ = extendabletype

    def deref(self, obj):
        """Dereference `obj' to concrete object."""
        raise NotImplementedError()

    def getcallable(self, translator, graphfunc, getconcretetype=None):
        """Return callable given a Python function."""
        if getconcretetype is None:
            getconcretetype = self.getconcretetype
        graph = translator.getflowgraph(graphfunc)
        llinputs = [getconcretetype(v) for v in graph.getargs()]
        lloutput = getconcretetype(graph.getreturnvar())

        typ, constr = self.callable_trait
        
        FT = typ(llinputs, lloutput)
        _callable = getattr(graphfunc, '_specializedversionof_', graphfunc)
        return constr(FT, graphfunc.func_name, graph = graph, _callable = _callable)

    def getconcretetype(self, v):
        """Helper called by getcallable() to get the conrete type of a variable
in a graph."""
        raise NotImplementedError()

class LowLevelTypeSystem(TypeSystem):
    callable_trait = (lltype.FuncType, lltype.functionptr)

    def __getattr__(self, name):
        """Lazy import to avoid circular dependencies."""
        if name == "rclass":
            from pypy.rpython.lltypesystem import rclass
            self.rclass = rclass

            return rclass
        elif name == "BUILTIN_TYPER":
            from pypy.rpython.lltypesystem import rbuiltin
            self.BUILTIN_TYPER = rbuiltin.BUILTIN_TYPER

            return self.BUILTIN_TYPER

    def deref(self, obj):
        assert isinstance(lltype.typeOf(obj), lltype.Ptr)
        return obj._obj

    def getconcretetype(self, v):
        return getattr(v, 'concretetype', lltype.Ptr(lltype.PyObject))

class ObjectOrientedTypeSystem(TypeSystem):
    callable_trait = (ootype.StaticMethod, ootype.static_meth)

    # FIXME rclass

    def deref(self, obj):
        assert isinstance(ootype.typeOf(obj), ootype.OOType)
        return obj

# All typesystems are singletons
LowLevelTypeSystem.instance = LowLevelTypeSystem()
ObjectOrientedTypeSystem.instance = ObjectOrientedTypeSystem()

# Multiple dispatch on type system and high-level annotation

from pypy.annotation.pairtype import pairtype
from pypy.annotation.model import SomeObject

class __extend__(pairtype(TypeSystem, SomeObject)):
    def rtyper_makerepr((ts, s_obj), rtyper):
        return s_obj.rtyper_makerepr(rtyper)

    def rtyper_makekey((ts, s_obj), rtyper):
        if hasattr(s_obj, "rtyper_makekey_ex"):
            return s_obj.rtyper_makekey_ex(rtyper)
        return s_obj.rtyper_makekey()
