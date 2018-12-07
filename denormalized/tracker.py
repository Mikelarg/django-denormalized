""" Tracking changes for denormalized fields."""

from typing import Optional, Iterable, Tuple

from django.db import models
from django.db.models import Count, Q, Sum, Min, F
from django.db.models.expressions import CombinedExpression

from denormalized.types import IncrementalUpdates

PREVIOUS_VERSION_FIELD = '_denormalized_previous_version'


class DenormalizedTracker:
    def __init__(self, field, aggregate=Count('*'), query=Q(),
                 callback=lambda obj: True, related_name=None):
        self.field = field
        self.aggregate = aggregate
        self.query = query
        self.callback = callback
        self.foreign_key = related_name

    def track_changes(self, instance=None, created=None, deleted=None
                      ) -> Iterable[Tuple[models.Model, IncrementalUpdates]]:
        changed = []
        try:
            foreign_object = getattr(instance, self.foreign_key)
        except models.ObjectDoesNotExist:
            # this may raise DNE while cascade deleting with Collector
            foreign_object = None
        is_suitable = self.callback(instance)
        delta = self._get_delta(instance)
        if created:
            if is_suitable:
                return self._update_value(foreign_object, delta, sign=1),
            return []
        elif deleted:
            if is_suitable:
                return self._update_value(foreign_object, delta, sign=-1),
            return []
        old_instance = getattr(instance, PREVIOUS_VERSION_FIELD)
        old_suitable = self.callback(old_instance)
        old_foreign_object = getattr(old_instance, self.foreign_key)

        sign = is_suitable - old_suitable
        if foreign_object == old_foreign_object and sign != 0:
            changed.append(self._update_value(foreign_object, delta, sign=sign))
        elif foreign_object != old_foreign_object:
            if old_suitable:
                changed.append(self._update_value(
                    old_foreign_object, old_delta, sign=-1))
            if is_suitable:
                changed.append(self._update_value(
                    foreign_object, delta, sign=1))
        else:
            # foreign_object == old_foreign_object and sign == 0
            changed.append(self._update_value(
                foreign_object, delta - old_delta, sign=1))

        return filter(None, changed)

    def _update_value(self, foreign_object, delta, sign=1
                      ) -> Optional[Tuple[models.Model, IncrementalUpdates]]:
        if delta == 0 or not foreign_object:
            return None
        return foreign_object, {self.field: F(self.field) + delta * sign}

    def _get_delta(self, instance, deleted: Optional[bool] = False):
        if isinstance(self.aggregate, Count):
            return 1
        elif isinstance(self.aggregate, Sum):
            arg = self.aggregate.source_expressions[0]
            value = getattr(instance, arg.name)
            if isinstance(value, CombinedExpression):
                instance.refresh_from_db(fields=(arg.name,))
                value = getattr(instance, arg.name)
            return value
        elif isinstance(self.aggregate, Min):
            arg = self.aggregate.source_expressions[0]
            value = getattr(instance, arg.name)
            foreign_object = getattr(instance, self.foreign_key)
            min_value = getattr(foreign_object, self.field)
            if deleted:
                # object is removed from foreign_object related list
                if min_value < value:
                    # non-minimal object is deleted, no update is needed
                    return 0
                # object that had min value is now deleted, full recompute
                # is required
                return self._get_full_aggregate(instance)
            elif deleted is False:
                # object is added to foreign_object related list
                if min_value is None:
                    return value
                else:
                    return min(min_value, value) - min_value
            else:
                # object changed itself without foreign_key changing
                return 0

        raise NotImplementedError()  # pragma: no cover

    def _get_full_aggregate(self, instance):
        # Computes full aggregate excluding passed instance
        raise NotImplementedError()
