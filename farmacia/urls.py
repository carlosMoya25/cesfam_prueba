
from django.urls import path
from . import views, importacion_views, traslados

app_name = "farmacia"

urlpatterns = [
    path("pendientes/", views.pendientes_conciliacion, name="pendientes_conciliacion"),
    path("importar/", importacion_views.importar_csv, name="importar_csv"),
    path("importar/plantilla/", importacion_views.plantilla_csv, name="plantilla_csv"),
    path("importar/<uuid:pk>/reporte/", importacion_views.reporte_importacion, name="reporte_importacion"),
    path("importar/<uuid:pk>/", importacion_views.revisar_importacion, name="revisar_importacion"),
    path("traslados/nuevo/", traslados.trasladar_a_anexo, name="trasladar_a_anexo"),
    path("historial/", views.historial, name="historial"),
    path(
        "",
        views.dashboard,
        name="dashboard",
    ),

    path(
        "entradas/nueva/",
        views.registrar_entrada,
        name="registrar_entrada",
    ),

    path(
        "medicamentos/",
        views.lista_medicamentos,
        name="lista_medicamentos",
    ),

    path(
        "medicamentos/nuevo/",
        views.crear_medicamento,
        name="crear_medicamento",
    ),

    path(
        "medicamentos/<int:pk>/",
        views.detalle_medicamento,
        name="detalle_medicamento",
    ),

    path(
        "medicamentos/<int:pk>/editar/",
        views.editar_medicamento,
        name="editar_medicamento",
    ),

    path(
        "medicamentos/<int:pk>/estado/",
        views.cambiar_estado_medicamento,
        name="cambiar_estado_medicamento",
    ),
]
