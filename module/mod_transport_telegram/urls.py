from django.urls import path

from . import views


urlpatterns = [
    path("", views.info, name="mod_transport_telegram_info"),
]
