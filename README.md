# 📦 ArgentOS Extrepo Manager

<p align="center">
  <img src="org.openargentos.ExtrepoManager.svg" width="128" alt="Logotipo de ArgentOS Extrepo Manager">
</p>

**Gestor visual de repositorios APT externos para [OpenArgentOS](https://github.com/Tavo78ok) (ArgentOS Platinum Edition).**

Interfaz gráfica GTK4 + libadwaita construida sobre [extrepo](https://salsa.debian.org/extrepo-team/extrepo), la herramienta oficial de Debian para gestionar repositorios de terceros de forma segura. Toda la acción con privilegios pasa por `pkexec` (polkit), nunca se usa `sudo` ni credenciales en claro.

---
<img width="1440" height="900" alt="Captura de pantalla de 2026-10-23 17-16-22" src="https://github.com/user-attachments/assets/9488dc79-40f4-463d-94d4-fe206007afd4" />
<img width="1440" height="900" alt="Captura de pantalla de 2026-10-23 17-16-47" src="https://github.com/user-attachments/assets/838a4333-e78b-49c0-80e5-befd7674a51f" />

## ✨ Características

- 🔍 **Buscar repositorios** del catálogo de extrepo por nombre, descripción o URL.
- ✅ **Habilitar / deshabilitar** repositorios con un clic (autenticación polkit).
- 🔄 **Actualizar el índice** de extrepo desde la barra de herramientas.
- 📥 **Instalar software** directamente del repositorio habilitado:
  - Búsqueda de paquetes con `apt-cache` (por nombre y por descripción).
  - Selección múltiple con casillas y botón «seleccionar todo».
  - `apt-get update` **opcional** antes de instalar (útil si el mirror está lento).
  - Instalación silenciosa y segura: `--no-install-recommends` + resolución de conflictos de conffiles sin bloquearse.
- 📈 **Filtros** «Todos / Activos» y contador de resultados en tiempo real.
- 🪟 Funciona **como aplicación independiente** o **como pestaña integrada en OpenDash**.

## 🧱 Tecnologías

| Componente | Detalle |
|---|---|
| Interfaz | GTK4 + libadwaita (Python 3.10+, PyGObject) |
| Backend | `extrepo`, `apt-cache`, `apt-get`, `dpkg-query` |
| Privilegios | `pkexec` (polkit) — nunca contraseñas en texto |
| Concurrencia | Todos los procesos bloqueantes corren en hilos separados con retornos por `GLib.idle_add` |
| Empaquetado | Paquete `.deb` nativo (`build_deb.sh`) |

## 📥 Instalación

### Desde el paquete .deb (recomendado)

```bash
sudo dpkg -i argentos-extrepo-manager_1.1.0_all.deb
sudo apt-get install -f   # resuelve dependencias si faltan
```

Dependencias: `python3 (>= 3.10)`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `extrepo`, `polkitd` y `apt`.

### Build desde origen

```bash
git clone https://github.com/Tavo78ok/argentos-extrepo-manager.git
cd argentos-extrepo-manager
bash build_deb.sh          # produce argentos-extrepo-manager_1.1.0_all.deb
```

> También puedes ejecutarlo sin instalar: `./argentos-extrepo-manager.py`

## 🖥️ Uso

1. **Busca** un repositorio (p. ej. `docker`, `elastic`, `tor`).
2. **Habilita** el repositorio con «Habilitar» (te pedirá autenticación polkit).
3. Aparece un toast con acción directa → **Instalar software** (o usa el botón de la fila).
4. **Selecciona** uno o más paquetes (o «seleccionar todo») y pulsa «Instalar seleccionados».
5. Los paquetes quedan marcados como *Instalado* en las búsquedas siguientes.

## 🌐 Integración con Argent OpenDash

El widget es autocontenido y embebible:

```python
from argentos_extrepo_manager import build_tab_widget

# Dentro de Argent-OpenDash, con su ToastOverlay existente:
page = build_tab_widget(toast_overlay=self._toast)
self._view_stack.add_titled(page, "extrepo", "Repositorios")
```

Los toasts de operación (habilitación, errores, instalación) se reportan al overlay del host si se provee; si no, se usan overlays locales.

## 📁 Estructura del proyecto

```
argent_extrepo-Manager/
├── argentos-extrepo-manager.py                     # App completa (monofichero)
├── build_deb.sh                                    # Empaqueta el .deb
├── org.openargentos.ExtrepoManager.desktop         # Entrada de escritorio
├── org.openargentos.ExtrepoManager.svg             # Ícono (128×128)
├── org.openargentos.ExtrepoManager-symbolic.svg    # Ícono simbólico (16×16)
└── argentos-extrepo-manager_1.1.0_all.deb          # Paquete compilado
```

## 🔒 Seguridad

- Nunca se ejecuta nada como root sin polkit: `pkexec` muestra el diálogo de autenticación del sistema.
- Los `.sources` generados los administra exclusivamente `extrepo`; la app solo lee estado y llama a `enable` / `disable`.
- `apt-get` se invoca con timeout de 300 s y salida capturada; errores visibles en la interfaz.

## 🗺️ Hoja de ruta

- [ ] Vista de detalles del repositorio (remoto, suites, llave GPG).
- [ ] Exportar/importar perfiles de repositorios.
- [ ] Notificaciones de actualizaciones disponibles en repos habilitados.

## 🤝 Contribuir

Los reportes de errores y propuestas son bienvenidos: abre un *issue* en el repositorio. Para cambios de código, mantén el estilo actual (GTK4/libadwaita, un solo fichero).

## 📄 Licencia

GPL-3.0+ — ver [`usr/share/doc/argentos-extrepo-manager/copyright`](build_deb.sh) incluido en el paquete.

---

<p align="center"><i>Hecho con 💙 para OpenArgentOS</i></p>
