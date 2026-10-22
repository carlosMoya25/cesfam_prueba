from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import F, Sum
from django.utils import timezone
from farmacia.models import Medicamento, StockLote
from usuarios.utils import puede_gestionar_farmacia

from requerimientos.models import Requerimiento
from inventario.models import Articulo, StockAnexo


@login_required
def dashboard(request):
    #grupos

    grupos = list(
    request.user.groups.values_list(
        "name",
        flat=True
    )
)

 
    # REQUERIMIENTOS
    

    total_requerimientos = Requerimiento.objects.count()

    pendientes = Requerimiento.objects.filter(
        estado__nombre__iexact="Pendiente"
    ).count()

    en_proceso = Requerimiento.objects.filter(
        estado__nombre__iexact="En proceso"
    ).count()

    resueltos = Requerimiento.objects.filter(
        estado__nombre__iexact="Resuelto"
    ).count()



    # INVENTARIO


    total_articulos = Articulo.objects.filter(
        activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True
    ).count()
    stock_bajo = StockAnexo.objects.filter(
        articulo__medicamento__isnull=True, articulo__saldos_pendientes_farmacia__isnull=True,
        cantidad__gt=0, cantidad__lte=F("articulo__stock_minimo"),
    ).count()

    total_medicamentos = unidades_farmacia = lotes_vencidos = 0
    stock_medicamentos_sin_lote = False
    if puede_gestionar_farmacia(request.user):
        hoy = timezone.localdate()
        stocks_farmacia = StockLote.objects.all()
        stock_utilizable = stocks_farmacia.filter(
            lote__activo=True,
            lote__medicamento__activo=True,
            lote__medicamento__articulo__activo=True,
            anexo__activo=True,
            lote__fecha_vencimiento__gte=hoy,
        )
        total_medicamentos = Medicamento.objects.filter(
            activo=True, articulo__activo=True
        ).count()
        unidades_farmacia = stock_utilizable.aggregate(total=Sum("cantidad"))["total"] or 0
        lotes_vencidos = stocks_farmacia.filter(
            cantidad__gt=0, lote__fecha_vencimiento__lt=hoy
        ).values("lote_id").distinct().count()
        stock_medicamentos_sin_lote = StockAnexo.objects.filter(
            articulo__medicamento__isnull=False, cantidad__gt=0
        ).exists()


    context = {
        "grupos": grupos,
        "total_requerimientos": total_requerimientos,
        "pendientes": pendientes,
        "en_proceso": en_proceso,
        "resueltos": resueltos,

        "total_articulos": total_articulos,
        "stock_bajo": stock_bajo,
        "total_medicamentos": total_medicamentos,
        "unidades_farmacia": unidades_farmacia,
        "lotes_vencidos": lotes_vencidos,
        "stock_medicamentos_sin_lote": stock_medicamentos_sin_lote,
    }

    return render(
        request,
        "core/dashboard.html",
        context
    )