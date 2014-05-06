import logging

from django.db.models.fields.related import ReverseSingleRelatedObjectDescriptor
from django.db.models.fields.related import ForeignRelatedObjectsDescriptor
from django.conf import settings

from specifyweb.specify.models import datamodel
from specifyweb.stored_queries import models
from specifyweb.stored_queries.fieldspec import FieldSpec
from specifyweb.stored_queries.query_ops import QueryOps
from specifyweb.stored_queries.views import build_query

logger = logging.getLogger(__name__)

class F(str):
    pass

class RelatedSearch(object):
    __fielspec_template = None
    __display_fieldspecs = None
    __filter_fieldspecs = None
    __root = None

    distinct = False
    filters = []
    excludes = []
    definitions = None
    columns = None

    @classmethod
    def execute(cls, session, config, terms, collection, limit, offset):
        queries = filter(None, (
            cls(defn).build_related_query(session, config, terms, collection)
            for defn in cls.definitions))

        if len(queries) > 0:
            query = queries[0].union(*queries[1:])
            count = query.count()
            results = list(query.limit(limit).offset(offset))
        else:
            count = 0
            results = []

        return {
            'totalCount': count,
            'results': results,
            'definition': {
                'name': cls.__name__,
                'root': cls.root().name,
                'columns': cls.columns}}

    @classmethod
    def root(cls):
        if cls.__root is None:
            cls.__root = datamodel.get_table(cls.definitions[0].split('.')[0])
        return cls.__root

    @classmethod
    def display_fieldspecs(cls):
        if cls.__display_fieldspecs is None:
            cls.__display_fieldspecs = [
                cls.fieldspec_template()._replace(
                    field_name=fieldname,
                    join_path=joinpath,
                    op_num=1,
                    value="",
                    display=True)
                for (fieldname, joinpath) in [cls.make_join_path(col.split('.'))
                                              for col in cls.columns]
            ]
        return cls.__display_fieldspecs

    @classmethod
    def filter_fieldspecs(cls):
        if cls.__filter_fieldspecs is None:
            def process_filter(f, negate):
                (field, op, val) = f
                op_num = QueryOps.OPERATIONS.index(op.__name__)
                value = cls.process_value(val)
                return cls.make_join_path(field.split('.')) + (op_num, value, negate)

            filters  = [process_filter(f, False) for f in cls.filters]
            excludes = [process_filter(f, True) for f in cls.excludes]

            cls.__filter_fieldspecs = [
                cls.fieldspec_template()._replace(
                    field_name=fieldname,
                    join_path=joinpath,
                    op_num=op_num,
                    value=value,
                    negate=negate,
                    display=False)
                for (fieldname, joinpath, op_num, value, negate)
                in filters + excludes]

        return cls.__filter_fieldspecs

    @classmethod
    def process_value(cls, val):
        if not isinstance(val, F):
            return val
        fieldname, joinpath = cls.make_join_path(val.split('.'))
        return cls.fieldspec_template()._replace(
            field_name=fieldname,
            join_path=joinpath,
            op_num=1,
            value="")


    @classmethod
    def fieldspec_template(cls):
        if cls.__fielspec_template is None:
            cls.__fieldspec_template = FieldSpec(
                field_name=None,
                date_part=None,
                root_table=getattr(models, cls.root().name),
                join_path=None,
                is_relation=False,
                op_num=None,
                value=None,
                negate=False,
                display=False,
                sort_type=0)
        return cls.__fieldspec_template

    @classmethod
    def make_join_path(cls, path):
        table = cls.root()
        logger.info("make_join_path from %s : %s" % (table, path))
        model = getattr(models, table.name)
        fieldname, join_path = None, []

        for element in path:
            field = table.get_field(element, strict=True)
            fieldname = field.name
            if field.is_relationship:
                table = datamodel.get_table(field.relatedModelName)
                model = getattr(models, table.name)
                join_path.append((fieldname, model))

        return fieldname, join_path

    def __init__(self, definition):
        self.definition = definition

    def make_fieldspec(self, primary_query):
        _, jp = self.make_join_path(self.definition.split('.')[1:])
        logger.debug('definition %s resulted in join path %s', self.definition, jp)

        return self.fieldspec_template()._replace(
            field_name=jp[-1][1]._id if jp else self.root().idFieldName,
            join_path=jp,
            op_num=QueryOps.OPERATIONS.index('op_in'),
            value=primary_query,
            display=False)

    def pivot(self):
        pivot = self.root()
        path = self.definition.split('.')[1:]
        while len(path):
            field = pivot.get_field(path[0], strict=True)
            pivot = datamodel.get_table(field.relatedModelName)
            path = path[1:]
        return pivot

    def build_related_query(self, session, config, terms, collection):
        from .views import build_primary_query

        pivot = self.pivot()
        for searchtable in config.findall('tables/searchtable'):
            if searchtable.find('tableName').text == pivot.name:
                break
        else:
            return None

        primary_query = build_primary_query(session, searchtable, terms, collection, as_scalar=True)

        if primary_query is None:
            return None

        field_specs = (self.display_fieldspecs() +
                       self.filter_fieldspecs() +
                       [self.make_fieldspec(primary_query)])

        related_query, _, _ = build_query(session, collection, self.root().tableId, field_specs)

        if self.distinct:
            related_query = related_query.distinct()

        return related_query
