from django.shortcuts import render

from . import media_library


def about(request):
    return render(request, "about.html", {
        "videos": media_library.VIDEOS,
        "audio": media_library.AUDIO,
        "benchmarks": media_library.BENCHMARKS,
        "partner_links": media_library.PARTNER_LINKS,
    })


def privacy(request):
    return render(request, "legal/privacy.html")


def terms(request):
    return render(request, "legal/terms.html")
