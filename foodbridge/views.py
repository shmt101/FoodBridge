from django.shortcuts import render

from . import media_library


def about(request):
    return render(request, "about.html", {
        "videos": media_library.VIDEOS,
        "audio": media_library.AUDIO,
        "benchmarks": media_library.BENCHMARKS,
        "partner_links": media_library.PARTNER_LINKS,
    })
