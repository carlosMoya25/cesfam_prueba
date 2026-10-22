from django.urls import path

from . import views, organizacion_views


app_name = "inventario"


urlpatterns = [
    path("", views.panel_inventario, name="panel"),
    path("departamentos/nuevo/", organizacion_views.crear_departamento, name="crear_departamento"),
    path("ubicaciones/nueva/", organizacion_views.crear_ubicacion, name="crear_ubicacion"),
    path("entrada/", views.entrada_inventario, name="entrada"),
    path("salida/", views.salida_inventario, name="salida"),
    path(
        "transferencia/",
        views.transferencia_inventario,
        name="transferencia",
    ),
    path(
        "historial/",
        views.historial_movimientos,
        name="historial",
    ),
    path("ajuste/", views.ajuste_inventario, name="ajuste"),
    path("categorias/", views.categorias, name="categorias"),
    path("categorias/nueva/", views.crear_categoria, name="crear_categoria"),
    path("categorias/<int:pk>/editar/", views.editar_categoria, name="editar_categoria"),
    path("articulos/", views.articulos, name="articulos"),
    path("articulos/nuevo/", views.crear_articulo, name="crear_articulo"),
    path("articulos/<int:pk>/editar/", views.editar_articulo, name="editar_articulo"),


    path(
        "stock/",
        views.consultar_stock,
        name="consultar_stock"
    ),
    
]