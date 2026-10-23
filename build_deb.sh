#!/bin/bash
# build_deb.sh — Empaqueta ArgentOS Extrepo Manager como .deb
# Solo necesitas: argentos-extrepo-manager.py + este script
# Uso: bash build_deb.sh

set -e

APP_NAME="argentos-extrepo-manager"
VERSION="1.1.0"
ARCH="all"
PKG_DIR="/tmp/${APP_NAME}_${VERSION}_${ARCH}"

echo "╔══════════════════════════════════════════════╗"
echo "║  ArgentOS Extrepo Manager — build .deb v${VERSION}  ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Verificar que el .py existe
if [ ! -f "argentos-extrepo-manager.py" ]; then
    echo "❌  No se encontró argentos-extrepo-manager.py en el directorio actual."
    echo "    Asegúrate de ejecutar este script desde la misma carpeta."
    exit 1
fi

echo "[1/5] Creando estructura de directorios..."
rm -rf "$PKG_DIR"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/usr/share/doc/${APP_NAME}"
mkdir -p "${PKG_DIR}/usr/lib/${APP_NAME}"

echo "[2/5] Copiando archivos..."
cp argentos-extrepo-manager.py "${PKG_DIR}/usr/lib/${APP_NAME}/argentos-extrepo-manager.py"
chmod 644 "${PKG_DIR}/usr/lib/${APP_NAME}/argentos-extrepo-manager.py"

# Iconos de la aplicación (app-id: org.openargentos.ExtrepoManager)
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/symbolic/apps"
cp org.openargentos.ExtrepoManager.svg \
   "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/org.openargentos.ExtrepoManager.svg"
cp org.openargentos.ExtrepoManager-symbolic.svg \
   "${PKG_DIR}/usr/share/icons/hicolor/symbolic/apps/org.openargentos.ExtrepoManager-symbolic.svg"
chmod 644 "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/org.openargentos.ExtrepoManager.svg" \
          "${PKG_DIR}/usr/share/icons/hicolor/symbolic/apps/org.openargentos.ExtrepoManager-symbolic.svg"

# Lanzador en /usr/bin
cat > "${PKG_DIR}/usr/bin/argentos-extrepo-manager" << 'LAUNCHER'
#!/bin/bash
exec python3 /usr/lib/argentos-extrepo-manager/argentos-extrepo-manager.py "$@"
LAUNCHER
chmod 755 "${PKG_DIR}/usr/bin/argentos-extrepo-manager"

# Entrada de escritorio (generada inline)
cat > "${PKG_DIR}/usr/share/applications/argentos-extrepo-manager.desktop" << 'DESKTOP'
[Desktop Entry]
Version=1.0
Type=Application
Name=Extrepo Manager
Name[es]=Extrepo Manager
GenericName=Gestor de Repositorios
GenericName[es]=Gestor de Repositorios Externos
Comment=Manage external APT repositories for ArgentOS
Comment[es]=Gestiona repositorios APT externos en ArgentOS Platinum
Exec=/usr/bin/argentos-extrepo-manager
Icon=org.openargentos.ExtrepoManager
Terminal=false
Categories=System;PackageManager;GTK;
Keywords=extrepo;repositorio;apt;debian;openargentos;argentos;
StartupNotify=true
StartupWMClass=org.openargentos.ExtrepoManager
DESKTOP
chmod 644 "${PKG_DIR}/usr/share/applications/argentos-extrepo-manager.desktop"

# Copyright
cat > "${PKG_DIR}/usr/share/doc/${APP_NAME}/copyright" << 'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: argentos-extrepo-manager
Upstream-Contact: https://github.com/Tavo78ok
License: GPL-3.0+
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.
COPYRIGHT

echo "[3/5] Generando control..."
cat > "${PKG_DIR}/DEBIAN/control" << CONTROL
Package: ${APP_NAME}
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: ArgentOS Team <el-tavo78@hotmail.com>
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, extrepo, polkitd | policykit-1
Recommends: extrepo-offline-data, apt (>= 1.4)
Section: admin
Priority: optional
Homepage: https://github.com/Tavo78ok/argentos-extrepo-manager
Description: Gestor visual de repositorios externos para ArgentOS
 ArgentOS Extrepo Manager proporciona una interfaz gráfica GTK4/libadwaita
 para gestionar repositorios APT externos mediante extrepo.
 .
 Permite buscar, habilitar, deshabilitar y actualizar repositorios
 de terceros de forma segura usando autenticación polkit. Una vez
 habilitado un repositorio, permite buscar e instalar paquetes
 directamente desde la misma interfaz con apt-get.
CONTROL

echo "[4/5] Generando postinst / postrm..."
cat > "${PKG_DIR}/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
fi
POSTINST
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

cat > "${PKG_DIR}/DEBIAN/postrm" << 'POSTRM'
#!/bin/bash
set -e
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications || true
fi
if [ "$1" = "purge" ] && command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
fi
POSTRM
chmod 755 "${PKG_DIR}/DEBIAN/postrm"

echo "[5/5] Construyendo .deb..."
dpkg-deb --build --root-owner-group "$PKG_DIR"
DEB_FILE="${APP_NAME}_${VERSION}_${ARCH}.deb"
mv "/tmp/${DEB_FILE}" "./${DEB_FILE}"

echo ""
echo "✅  Listo: ./${DEB_FILE}"
echo "    Instalar:      sudo dpkg -i ${DEB_FILE}"
echo "    Dependencias:  sudo apt-get install -f"
