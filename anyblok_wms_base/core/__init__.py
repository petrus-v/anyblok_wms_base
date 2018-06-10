# -*- coding: utf-8 -*-
# This file is a part of the AnyBlok / WMS Base project
#
#    Copyright (C) 2018 Georges Racinet <gracinet@anybox.fr>
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file,You can
# obtain one at http://mozilla.org/MPL/2.0/.
from anyblok.blok import Blok
from . import goods


class WmsCore(Blok):
    """Core concepts for WMS and logistics.
    """
    version = '0.0.1'
    author = "Georges Racinet"

    @classmethod
    def import_declaration_module(cls):
        from . import wms  # noqa
        from . import location  # noqa
        from . import operation  # noqa
        goods.import_declarations()

    @classmethod
    def reload_declaration_module(cls, reload):
        from . import wms
        reload(wms)
        from . import location
        reload(location)
        from . import operation
        reload(operation)
        operation.reload_declarations(reload)
        goods.import_declarations(reload=reload)
