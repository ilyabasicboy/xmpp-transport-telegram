from django.shortcuts import render


def info(request):
    return render(request, "mod_transport_telegram/info.html")
