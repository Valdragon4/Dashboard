from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("finance.urls")),
]

handler400 = "finance.views.error_400"
handler403 = "finance.views.error_403"
handler404 = "finance.views.error_404"
handler500 = "finance.views.error_500"


