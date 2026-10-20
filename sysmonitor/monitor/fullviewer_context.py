def fullviewer_context(request):
    from .fullviewer import is_fullviewer
    return {'is_fullviewer': is_fullviewer(getattr(request,'user',None))}
