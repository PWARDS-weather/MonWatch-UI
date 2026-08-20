def filter(points, max_points=14):
    if len(points) <= max_points:
        return list(points)

    priorities = []
    for pt in points:
        rank = 0
        if pt.priority > 0:
            rank -= abs(pt.priority)
        if pt.intensity:
            rank -= (100 - min(pt.intensity, 140))
        if pt.hour == 0:
            rank -= 1000
        elif pt.hour <= 24:
            rank -= 50
        priorities.append(rank)

    sorted_indices = sorted(range(len(points)), key=lambda i: priorities[i])
    kept_indices = set(sorted_indices[:max_points])
    result = [pt for i, pt in enumerate(points) if i in kept_indices]
    result.sort(key=lambda p: p.id)
    return result
