class Singleton(type):
    """
    A basic singleton to be used as a metaclass
    
    Credit goes to:
    http://stackoverflow.com/questions/6760685/creating-a-singleton-in-python

    Used as a metaclass, not a base class:

    class MyClass(object):
      __metaclass__ = Singleton
    """
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]