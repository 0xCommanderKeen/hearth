type Pointer = {
  pointerId: number;
  clientX: number;
  clientY: number;
  button: number;
};
// Movement is sticky: returning to the starting point is still a drag.
export function selectionGesture() {
  let down: Pointer | undefined;
  let dragged = false;
  return {
    down(event: Pointer) {
      if (down) {
        dragged = true;
        return;
      }
      down = {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        button: event.button,
      };
      dragged = event.button !== 0;
    },
    move(event: Pointer) {
      if (
        down &&
        (event.pointerId !== down.pointerId ||
          Math.hypot(
            event.clientX - down.clientX,
            event.clientY - down.clientY,
          ) > 5)
      )
        dragged = true;
    },
    cancel() {
      down = undefined;
      dragged = true;
    },
    up(event: Pointer) {
      const selected =
        !!down &&
        !dragged &&
        event.pointerId === down.pointerId &&
        event.button === 0 &&
        Math.hypot(
          event.clientX - down.clientX,
          event.clientY - down.clientY,
        ) <= 5;
      down = undefined;
      return selected;
    },
  };
}
