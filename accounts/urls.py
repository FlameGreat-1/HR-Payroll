from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
]

handler404 = "accounts.views.handler404"
handler500 = "accounts.views.handler500"
handler403 = "accounts.views.handler403"
