"""Quasistatic settling of circular grains in a section of the packed material."""
from math import floor, pi, sqrt


def settled_section(width, length, radii, keys, supply):
    """Deposit one disk per arrival and roll downhill until contact supports it."""
    cell = 2. * float(radii.max())
    bins = {}
    tops = {}
    packed = []
    area = 0.
    for key, feed in zip(keys, supply):
        r = float(radii[key])
        if width <= r:
            continue
        x = (2.*feed-1.)*(width-r)
        y = r
        support = None
        cols = range(floor((x-cell)/cell), floor((x+cell)/cell)+1)
        row = max((tops.get(col,-1) for col in cols), default=-1)
        while row >= 0 and (row+1)*cell+cell >= y:
            for col in cols:
                for disk in bins.get((col,row), ()):
                    dx = disk[0]-x
                    rr = disk[2]+r
                    if abs(dx) < rr:
                        contact = disk[1]+sqrt(rr*rr-dx*dx)
                        if contact > y:
                            y,support = contact,disk
            row -= 1
        if support is not None:
            sign = 1 if x >= support[0] else -1
        while support is not None:
            ax,ay,ar = support
            ra = r+ar
            edge = min(width-r,ax+ra) if sign>0 else max(r-width,ax-ra)
            end_y = ay+sqrt(max(0.,ra*ra-(edge-ax)**2))
            hit = None
            # Intersect the downhill contact arc with nearby expanded circles.
            cols = range(floor((min(x,edge)-cell)/cell),floor((max(x,edge)+cell)/cell)+1)
            rows = range(floor((end_y-cell)/cell),floor((y+cell)/cell)+1)
            for col in cols:
                for row in rows:
                    for disk in bins.get((col,row), ()):
                        if disk is support:
                            continue
                        bx,by,br = disk
                        dx,dy = bx-ax,by-ay
                        d2 = dx*dx+dy*dy
                        rb = r+br
                        if d2 <= (ra-rb)**2 or d2 > (ra+rb)**2:
                            continue
                        f = .5*(1.+(ra*ra-rb*rb)/d2)
                        k = sqrt(max(0.,ra*ra/d2-f*f))
                        # Intersection above the directed line of centres.
                        # The opposite one is a contact on the inaccessible side.
                        k *= sign
                        cx,cy = ax+f*dx-k*dy,ay+f*dy+k*dx
                        if cy < ay-1.e-9 or cy > y+1.e-9:
                            continue
                        if sign*(cx-x) > 1.e-9 and sign*(edge-cx) >= 0.:
                            edge,end_y,hit = cx,cy,disk
            if end_y <= r:
                x = ax+sign*sqrt(max(0.,ra*ra-(r-ay)**2))
                y = r
                break
            x,y = edge,end_y
            if hit is not None:
                if sign*(hit[0]-x) >= 0.:
                    break
                support = hit
                continue
            if abs(abs(x)+r-width) < 1.e-8:
                break
            # Rolled off an isolated grain. Drop vertically to the next
            # support in this column and continue downhill on that support.
            former = support
            support = None
            landing = r
            for col in range(floor((x-cell)/cell),floor((x+cell)/cell)+1):
                for row in range(floor((y+cell)/cell),-1,-1):
                    if (row+2)*cell < landing:
                        break
                    for disk in bins.get((col,row), ()):
                        if disk is former:
                            continue
                        dx = x-disk[0]
                        rr = r+disk[2]
                        if abs(dx) < rr:
                            at = disk[1]+sqrt(rr*rr-dx*dx)
                            if landing < at <= y+1.e-8:
                                landing,support = at,disk
            y = landing
            if support is not None:
                sign = 1 if x >= support[0] else -1
        disk = float(x),float(y),r
        col,row = floor(x/cell),floor(y/cell)
        bins.setdefault((col,row),[]).append(disk)
        tops[col] = max(tops.get(col,-1),row)
        packed.append((*disk,key))
        area += pi*r*r
        if area >= 2.*width*length:
            break
    return packed


def support_paths(packed):
    """Latest support path from the base through touching disks.

    Axial transport clears both lower contacts. A wall supports no weight
    on its own. Keep the origin and distance of the last support to clear.
    """
    if not packed:
        return []
    step = 2.*max(row[2] for row in packed)
    cells = {}
    paths = []
    for x,y,r,_ in packed:
        col,row = floor(x/step),floor(y/step)
        latest = -1.
        foot = x
        if y <= r + 1.e-8:
            latest = y
        else:
            for cx in range(col-1,col+2):
                for cy in (row-1,row):
                    for px,py,pr,path,base in cells.get((cx,cy),()):
                        dy = y-py
                        if dy <= 0.:
                            continue
                        distance = r+pr
                        if abs((x-px)**2+dy**2-distance**2) < 1.e-7*step**2:
                            if path+distance > latest:
                                latest, foot = path+distance, base
        if latest < 0.:
            raise ValueError("Packed disk has no supporting contact")
        cells.setdefault((col,row),[]).append((x,y,r,latest,foot))
        paths.append((latest,foot))
    return paths
